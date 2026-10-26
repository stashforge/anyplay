importScripts('config.js', 'executor.js');

const executor = AnyplayExecutor.create(ANYPLAY_CONFIG);
let busy = false, ownedTab = null, bound = false;

async function broker(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    return await fetch(ANYPLAY_CONFIG.endpoint + path, {
      ...options, signal: controller.signal, redirect: 'error', cache: 'no-store',
      headers: {Authorization: 'Bearer ' + ANYPLAY_CONFIG.token,
        'Content-Type': 'application/json'},
    });
  } finally { clearTimeout(timer); }
}

async function rememberClosed() {
  ownedTab = null;
  bound = true;
  await chrome.storage.session.set({ownership: {bound: true, tabId: null}});
}

async function ownedTabSnapshot() {
  const id = ownedTab;
  if (id === null) return null;
  let tab;
  try { tab = await chrome.tabs.get(id); }
  catch (error) {
    const tabs = await chrome.tabs.query({});
    if (tabs.some(tab => tab.id === id)) throw error;
    await rememberClosed();
    return null;
  }
  const saved = (await chrome.storage.session.get('ownership')).ownership;
  if (ownedTab !== id || !saved?.bound || saved.tabId !== id) return null;
  return tab;
}

function clearanceOrigin(value) {
  if (typeof value !== 'string' || value.length > 8192
      || /[\x00-\x20\x7f\\]/.test(value)
      || /[\x00-\x1f\x7f\\]/.test(decodeURIComponent(value))) throw new Error('invalid_clearance_url');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.port || value.includes('#')
      || !ANYPLAY_CONFIG.origins.includes(url.origin)) throw new Error('invalid_clearance_origin');
  return url.origin;
}

async function clearanceSnapshot(payload, tab) {
  if (Object.keys(payload).sort().join(',') !== 'operation,url') throw new Error('invalid_clearance_fields');
  const origin = clearanceOrigin(payload.url);
  if (clearanceOrigin(tab.url) !== origin) throw new Error('wrong_clearance_tab');
  const stores = await chrome.cookies.getAllCookieStores();
  const store = stores.find(store => store.tabIds.includes(tab.id));
  if (!store) throw new Error('missing_clearance_store');
  const {partitionKey} = await chrome.cookies.getPartitionKey({tabId: tab.id, frameId: 0});
  const filter = {url: payload.url, name: 'cf_clearance', storeId: store.id};
  const partitioned = partitionKey ? await chrome.cookies.getAll({...filter, partitionKey}) : [];
  const cookies = partitioned.length ? partitioned : await chrome.cookies.getAll(filter);
  const identity = await chrome.scripting.executeScript({target: {tabId: tab.id}, world: 'MAIN',
    func: () => ({user_agent: navigator.userAgent, url: location.href})});
  const current = await ownedTabSnapshot();
  if (!current || current.id !== tab.id || clearanceOrigin(current.url) !== origin
      || clearanceOrigin(identity[0]?.result?.url) !== origin) throw new Error('clearance_tab_changed');
  return {cookies, user_agent: identity[0].result.user_agent,
    partition_key: partitioned.length ? partitionKey : null};
}

async function poll() {
  if (busy) return;
  busy = true;
  try {
    // Extension API activity resets the worker idle timer; ordinary fetch does not.
    const saved = (await chrome.storage.session.get('ownership')).ownership;
    if (saved?.bound) {
      bound = true;
      ownedTab = saved.tabId;
    }
    if (!bound) {
      const startup = new URL(ANYPLAY_CONFIG.startup_url).href;
      if (ANYPLAY_CONFIG.create_window === true) {
        const url = new URL(startup);
        if (url.protocol !== 'https:' || !ANYPLAY_CONFIG.origins.includes(url.origin)) return;
        // Claim ownership before creating: worker restart must not duplicate a
        // window if it stopped between native creation and saving the tab ID.
        await rememberClosed();
        const window = await chrome.windows.create({url: startup, type: 'popup',
          state: 'minimized', focused: false});
        const id = window?.tabs?.[0]?.id;
        if (!Number.isInteger(id)) return;
        ownedTab = id;
      } else {
        const tabs = await chrome.tabs.query({});
        const matches = tabs.filter(tab => tab.url === startup);
        if (matches.length !== 1) return;
        ownedTab = matches[0].id;
        bound = true;
      }
      await chrome.storage.session.set({ownership: {bound: true, tabId: ownedTab}});
    }
    const response = await broker('/command');
    if (response.status !== 200) return;
    const command = await response.json();
    let result;
    try {
      const tab = await ownedTabSnapshot();
      if (tab === null) {
        result = {error: 'browser_closed'};
      } else if (command.payload?.operation === 'clearance') {
        result = await clearanceSnapshot(command.payload, tab);
      } else if (command.payload?.operation === 'window') {
        const payload = command.payload;
        if (Object.keys(payload).sort().join(',') !== 'operation,state'
            || !['normal', 'minimized'].includes(payload.state)) throw new Error('invalid_window_state');
        const options = {state: payload.state};
        if (payload.state === 'normal') {
          options.focused = true;
          options.width = 760;
          options.height = 640;
        }
        const window = await chrome.windows.update(tab.windowId, options);
        result = ownedTab === tab.id ? {state: window.state} : {error: 'browser_closed'};
      } else {
        result = await executor.run(command.payload, ownedTab);
      }
    }
    catch (_) { result = {error: ownedTab === null ? 'browser_closed' : 'browser_operation_failed'}; }
    await broker('/result', {method: 'POST', body: JSON.stringify({id: command.id, result})});
  } catch (_) {
    // No cookies, endpoint, tokens, page text or exception dumps in diagnostics.
  } finally { busy = false; }
}

chrome.tabs.onRemoved.addListener(async id => {
  if (id === ownedTab) { ownedTab = null; bound = true; }
  try {
    const saved = (await chrome.storage.session.get('ownership')).ownership;
    if (saved?.bound && id === saved.tabId) {
      await rememberClosed();
    }
  } catch (_) {}
});
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === 'anyplay-poll') return poll();
});
chrome.alarms.create('anyplay-poll', {periodInMinutes: 0.5}).catch(() => {});
setInterval(poll, 250);
poll();
