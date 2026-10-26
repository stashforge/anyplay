const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const file = path.join(__dirname, '../media_cli/services/browser_access/assets/executor.js');
const context = {URL, TextEncoder, TextDecoder, AbortController, setTimeout, clearTimeout};
context.globalThis = context;
let calls = 0, suppliedOptions, injectedArgs;
const page = {
  ...context, location: {href: 'https://site.test/'},
  document: {documentElement: {outerHTML: '<html>ready</html>'}},
  fetch: async (url, options) => {
    suppliedOptions = options;
    const bytes = new TextEncoder().encode('hello');
    let sent = false;
    return {url, status: 200, body: {getReader: () => ({
      read: async () => sent ? {done: true} : (sent = true, {done: false, value: bytes}),
      cancel: async () => {}, releaseLock: () => {},
    })}};
  },
};
context.chrome = {
  tabs: {get: async id => ({id, url: page.location.href})},
  scripting: {executeScript: async options => {
    calls++;
    injectedArgs = options.args;
    assert.equal(options.world, 'MAIN');
    assert.equal(options.target.tabId, 42);
    const func = vm.runInNewContext('(' + options.func.toString() + ')', page);
    return [{result: await func(...options.args)}];
  }},
};
vm.runInNewContext(fs.readFileSync(file, 'utf8'), context);
const executor = context.AnyplayExecutor.create({origins: ['https://site.test'], max_body_bytes: 64});
const fetchCommand = {operation: 'fetch', url: 'https://site.test/api?q=oshi%20no%20ko',
  method: 'GET', redirect: 'error', max_body_bytes: 64};

(async () => {
  for (const url of ['https://evil.test/', 'https://sub.site.test/', 'https://site.test.evil.test/',
    'http://site.test/', 'https://user@site.test/', 'https://site.test:444/',
    'https://site.test/#x', 'https://site.test/\nfoo', 'https://site.test/%0afoo',
    'https://site.test/%5cfoo']) {
    await assert.rejects(() => executor.run({...fetchCommand, url}, 42));
  }
  for (const change of [{operation: 'evaluate'}, {method: 'POST'}, {redirect: 'follow'},
    {script: 'alert(1)'}, {max_body_bytes: 65}, {headers: {Cookie: 'secret'}}]) {
    await assert.rejects(() => executor.run({...fetchCommand, ...change}, 42));
  }
  assert.equal(calls, 0, 'invalid commands must never reach the page');
  const result = await executor.run(fetchCommand, 42);
  assert.equal(result.body, 'hello');
  assert.equal(suppliedOptions.redirect, 'error');
  assert.equal(suppliedOptions.credentials, 'include');
  assert(suppliedOptions.signal);
  assert(!JSON.stringify(injectedArgs).includes('token'));

  const document = await executor.run({operation: 'document', url: 'https://site.test/', max_body_bytes: 64}, 42);
  assert.equal(document.body, '<html>ready</html>');
  await assert.rejects(() => executor.run({operation: 'document', url: 'https://site.test/other', max_body_bytes: 64}, 42));
  page.location.href = 'https://evil.test/';
  const before = calls;
  await assert.rejects(() => executor.run(fetchCommand, 42));
  assert.equal(calls, before);
  page.location.href = 'https://site.test/';
  page.fetch = async () => ({url: 'https://evil.test/', status: 200});
  await assert.rejects(() => executor.run(fetchCommand, 42));
  page.fetch = async url => ({url, status: 403, headers: {get: name => name === 'cf-mitigated' ? 'challenge' : null}});
  await assert.rejects(() => executor.run(fetchCommand, 42), 'header-only challenge must not look verified');
  page.fetch = async url => ({url, status: 200, body: {getReader: () => ({
    read: async () => ({done: false, value: new Uint8Array(65)}),
    cancel: async () => {}, releaseLock: () => {},
  })}});
  await assert.rejects(() => executor.run(fetchCommand, 42));
  page.document.documentElement.outerHTML = '\u00e9'.repeat(33);
  await assert.rejects(() => executor.run({operation: 'document', url: 'https://site.test/', max_body_bytes: 64}, 42));
  await workerChecks();
  await clearanceChecks();
  await quietWindowChecks();
  console.log('executor checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

async function workerChecks() {
  let poll, removed, alarm, tabs = [{id: 99, url: 'https://other.test/'}], requests = [], runs = [];
  let payload = {operation: 'fetch'}, windowCalls = [];
  let stored = {}, storageReads = 0;
  const config = {endpoint: 'http://127.0.0.1:12345', token: 'private-token', startup_url: 'https://SITE.test:443'};
  const sandbox = {
    ANYPLAY_CONFIG: config, importScripts: () => {}, AbortController, URL, setTimeout, clearTimeout,
    setInterval: callback => {poll = callback;},
    AnyplayExecutor: {create: () => ({run: async (command, tab) => {runs.push(tab); return {body: 'ok'};}})},
    chrome: {
      tabs: {query: async () => tabs, get: async id => {
        assert.equal(id, 42); return {id, windowId: 77};
      }, onRemoved: {addListener: callback => {removed = callback;}}},
      windows: {update: async (id, options) => {windowCalls.push({id, options}); return {state: options.state};}},
      storage: {session: {get: async () => {storageReads++; return stored;}, set: async data => {stored = data;}}},
      alarms: {create: async () => {}, onAlarm: {addListener: callback => {alarm = callback;}}},
    },
    fetch: async (url, options) => {
      requests.push({url, options});
      assert.equal(options.headers.Authorization, 'Bearer private-token');
      assert.equal(options.redirect, 'error');
      return {status: 200, json: async () => ({id: 'one', payload})};
    },
  };
  const source = fs.readFileSync(path.join(path.dirname(file), 'worker.js'), 'utf8');
  vm.runInNewContext(source, sandbox);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 0, 'must not select unrelated tab');
  tabs = [{id: 1, url: 'https://site.test/'}, {id: 2, url: 'https://site.test/'}];
  await poll();
  assert.equal(requests.length, 0, 'ambiguous startup tabs must not be selected');
  tabs = [{id: 42, url: 'https://site.test/'}];
  await poll();
  assert.deepEqual(runs, [42]);
  assert.equal(requests.length, 2);
  assert.equal(JSON.parse(requests[1].options.body).id, 'one');
  assert(storageReads >= 3, 'polling must call extension API to reset inactivity');
  payload = {operation: 'window', state: 'normal'};
  await poll();
  assert.equal(windowCalls[0].id, 77);
  assert.equal(windowCalls[0].options.state, 'normal');
  assert.equal(windowCalls[0].options.focused, true);
  assert.equal(windowCalls[0].options.width, 760);
  assert.equal(windowCalls[0].options.height, 640);
  assert.deepEqual(runs, [42], 'window controls must never execute inside the page');
  for (const invalid of [{operation: 'window', state: 'fullscreen'},
    {operation: 'window', state: 'normal', windowId: 999}]) {
    payload = invalid;
    await poll();
    assert.equal(JSON.parse(requests.at(-1).options.body).result.error, 'browser_operation_failed');
  }
  assert.equal(windowCalls.length, 1);
  const oldLookup = sandbox.chrome.tabs.get;
  sandbox.chrome.tabs.get = async id => {
    const snapshot = await oldLookup(id);
    await removed(id);
    return snapshot;
  };
  payload = {operation: 'window', state: 'normal'};
  await poll();
  assert.equal(windowCalls.length, 1, 'tab closure during lookup must prevent focusing the window');
  assert.equal(JSON.parse(requests.at(-1).options.body).result.error, 'browser_closed');
  stored = {ownership: {bound: true, tabId: 42}};
  tabs = [];
  sandbox.chrome.tabs.get = async () => {throw new Error('No tab with id: 42.');};
  await poll();
  assert.equal(JSON.parse(requests.at(-1).options.body).result.error, 'browser_closed');
  assert.equal(stored.ownership.tabId, null, 'missing tab must retain closed ownership');
  sandbox.chrome.tabs.get = oldLookup;
  await removed(42);
  tabs = [{id: 43, url: 'https://site.test/'}];
  await poll();
  assert.deepEqual(runs, [42], 'must not adopt replacement tab after close');
  assert.equal(JSON.parse(requests.at(-1).options.body).result.error, 'browser_closed');
  vm.runInNewContext(source, {...sandbox});
  await new Promise(resolve => setImmediate(resolve));
  await poll();
  assert.deepEqual(runs, [42], 'worker restart must retain closed ownership');
  assert.equal(typeof alarm, 'function', 'alarm must wake a suspended worker');
  await alarm({name: 'anyplay-poll'});
  assert.deepEqual(runs, [42]);
}

async function quietWindowChecks() {
  let poll, removed, saved = {}, creates = [], results = [];
  const sandbox = {
    ANYPLAY_CONFIG: {endpoint: 'http://127.0.0.1:12345', token: 'private-token',
      startup_url: 'https://site.test/', origins: ['https://site.test'], create_window: true},
    importScripts: () => {}, AbortController, URL, setTimeout, clearTimeout,
    setInterval: callback => {poll = callback;},
    AnyplayExecutor: {create: () => ({run: async () => ({body: 'ok'})})},
    chrome: {
      tabs: {get: async id => ({id, url: 'https://site.test/', windowId: 77}),
        query: async () => [], onRemoved: {addListener: callback => {removed = callback;}}},
      windows: {
        create: async options => {creates.push(options); return {id: 77, tabs: [{id: 42}]};},
        update: async (id, options) => ({state: options.state}),
      },
      storage: {session: {get: async () => saved, set: async value => {saved = value;}}},
      alarms: {create: async () => {}, onAlarm: {addListener: () => {}}},
    },
    fetch: async (url, options) => {
      if (url.endsWith('/result')) results.push(JSON.parse(options.body).result);
      return {status: 200, json: async () => ({id: 'one', payload: {operation: 'fetch'}})};
    },
  };
  const source = fs.readFileSync(path.join(path.dirname(file), 'worker.js'), 'utf8');
  vm.runInNewContext(source, sandbox);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(creates.length, 1, 'helper must create its own quiet window');
  assert.equal(creates[0].state, 'minimized');
  assert.equal(creates[0].focused, false);
  assert.equal(creates[0].type, 'popup');
  assert.equal(creates[0].url, 'https://site.test/');
  assert(!Object.hasOwn(creates[0], 'width'), 'Chromium disallows size with minimized creation');
  await poll();
  assert.equal(creates.length, 1);
  vm.runInNewContext(source, {...sandbox});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(creates.length, 1, 'worker restart must reuse owned window');
  await removed(42);
  await poll();
  assert.equal(creates.length, 1, 'closed window must not reopen');
  assert.equal(results.at(-1).error, 'browser_closed');
  // A worker stopped during creation retains its claim, not a new window.
  saved = {};
  sandbox.chrome.windows.create = async options => {
    creates.push(options);
    throw new Error('creation interrupted');
  };
  vm.runInNewContext(source, {...sandbox});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(creates.length, 2);
  vm.runInNewContext(source, {...sandbox});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(creates.length, 2, 'interrupted creation must not duplicate windows');
  saved = {};
  sandbox.chrome.windows.create = async options => {
    creates.push(options);
    return {id: 77, tabs: [{id: 42}]};
  };
  sandbox.chrome.storage.session.set = async value => {
    if (value.ownership.tabId === 42) throw new Error('worker stopped before saving created tab');
    saved = value;
  };
  vm.runInNewContext(source, {...sandbox});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(creates.length, 3, 'native window was created before interruption');
  vm.runInNewContext(source, {...sandbox});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(creates.length, 3, 'successful native creation must not duplicate after lost tab save');
  saved = {};
  sandbox.ANYPLAY_CONFIG = {...sandbox.ANYPLAY_CONFIG, startup_url: 'https://evil.test/'};
  vm.runInNewContext(source, {...sandbox});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(creates.length, 3, 'unapproved startup URL must not open');
}

async function clearanceChecks() {
  let poll, payload = {operation: 'clearance', url: 'https://site.test/api'};
  let tabUrl = 'https://site.test/', results = [], filters = [], stored = {};
  const cookie = {name: 'cf_clearance', value: 'test-only-secret'};
  const sandbox = {
    ANYPLAY_CONFIG: {endpoint: 'http://127.0.0.1:12345', token: 'private-token',
      startup_url: 'https://site.test/', origins: ['https://site.test']},
    importScripts: () => {}, AbortController, URL, setTimeout, clearTimeout,
    setInterval: callback => {poll = callback;},
    AnyplayExecutor: {create: () => ({run: async () => ({error: 'unsupported'})})},
    chrome: {
      tabs: {get: async id => ({id, url: tabUrl}), query: async () => [{id: 42, url: tabUrl}],
        onRemoved: {addListener: () => {}}},
      storage: {session: {get: async () => stored, set: async value => {stored = value;}}},
      alarms: {create: async () => {}, onAlarm: {addListener: () => {}}},
      scripting: {executeScript: async options => {
        assert.equal(options.target.tabId, 42);
        assert.equal(options.world, 'MAIN');
        return [{result: {user_agent: 'actual-page-UA', url: tabUrl}}];
      }},
      cookies: {
        getAllCookieStores: async () => [{id: 'wrong', tabIds: [99]}, {id: 'right', tabIds: [42]}],
        getPartitionKey: async options => {
          assert.equal(options.tabId, 42); assert.equal(options.frameId, 0);
          return {partitionKey: {topLevelSite: 'https://site.test', hasCrossSiteAncestor: false}};
        },
        getAll: async options => {
          filters.push(options);
          return options.partitionKey ? [cookie] : [];
        },
      },
    },
    fetch: async (url, options) => {
      if (url.endsWith('/result')) results.push(JSON.parse(options.body).result);
      return {status: 200, json: async () => ({id: 'one', payload})};
    },
  };
  const source = fs.readFileSync(path.join(path.dirname(file), 'worker.js'), 'utf8');
  vm.runInNewContext(source, sandbox);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(results.at(-1).cookies[0].name, 'cf_clearance');
  assert.equal(results.at(-1).user_agent, 'actual-page-UA');
  assert.equal(results.at(-1).partition_key.topLevelSite, 'https://site.test');
  assert(filters.every(filter => filter.name === 'cf_clearance' && filter.storeId === 'right'));
  const before = filters.length;
  for (const invalid of [{operation: 'clearance', url: 'https://evil.test/'},
    {operation: 'clearance', url: 'https://site.test/', name: 'unrelated'},
    {operation: 'clearance', url: 'http://site.test/'},
    {operation: 'clearance', url: 'https://user@site.test/'},
    {operation: 'clearance', url: 'https://site.test/#fragment'}]) {
    payload = invalid;
    await poll();
    assert.equal(results.at(-1).error, 'browser_operation_failed');
  }
  assert.equal(filters.length, before, 'invalid clearance request must not read cookies');
  payload = {operation: 'clearance', url: 'https://site.test/api'};
  tabUrl = 'https://evil.test/';
  await poll();
  assert.equal(filters.length, before, 'navigated bound tab must not export cookies');
  tabUrl = 'https://site.test/';
  sandbox.chrome.cookies.getAll = async options => {
    tabUrl = 'https://evil.test/';
    return [cookie];
  };
  await poll();
  assert.equal(results.at(-1).error, 'browser_operation_failed', 'navigation during export must fail closed');
}
