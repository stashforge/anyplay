// The downloaded runtime has no direct filesystem, network or process access.
const vm = require('node:vm');
const readline = require('node:readline');
const {webcrypto} = require('node:crypto');
const pending = new Map();
let initialized = false;
let serial = 0;
function emit(value) {
  const line = JSON.stringify(value);
  if (line.length > 1048576) throw new Error('Runtime response too large');
  process.stdout.write(line + '\n');
}
const input = readline.createInterface({input: process.stdin});
input.on('line', async line => {
  try {
    const message = JSON.parse(line);
    if (initialized) {
      const resolve = pending.get(message.id);
      if (resolve) {
        pending.delete(message.id);
        resolve(new Response(Buffer.from(message.body, 'base64'), {
          status: message.status, headers: message.headers,
        }));
      }
      return;
    }
    initialized = true;
    const sandbox = {
      console: {log() {}, warn() {}, error() {}},
      URL, URLSearchParams, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer,
      crypto: webcrypto,
      atob: value => Buffer.from(value, 'base64').toString('binary'),
      btoa: value => Buffer.from(value, 'binary').toString('base64'),
      navigator: {userAgent: message.userAgent, language: 'en-US', languages: ['en-US']},
      location: {origin: 'https://senshi.to', href: 'https://senshi.to/',
        hostname: 'senshi.to', protocol: 'https:'},
      performance: {now: () => 1000},
      setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
      fetch: (url, options = {}) => new Promise(resolve => {
        const id = ++serial;
        pending.set(id, resolve);
        emit({kind: 'fetch', id, url: String(url), method: options.method || 'GET',
          headers: Object.fromEntries(new Headers(options.headers || {})),
          body: options.body ? Buffer.from(options.body).toString('base64') : null});
      }),
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.document = {cookie: '', referrer: '', currentScript: null,
      querySelector: () => null, addEventListener() {},
      createElement: () => ({style: {}, getContext: () => null, addEventListener() {}}),
      head: {appendChild() {}}, documentElement: {}};
    const context = vm.createContext(sandbox, {codeGeneration: {strings: false, wasm: true}});
    new vm.Script(message.runtime).runInContext(context, {timeout: 2000});
    sandbox.remoteId = message.remoteId;
    const result = await new vm.Script('window.__oct.open(remoteId)').runInContext(context, {timeout: 2000});
    emit({kind: 'result', result});
  } catch {
    emit({kind: 'error'});
  }
});
