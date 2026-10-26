/* Fixed capabilities only. Never put bridge credentials in page arguments. */
(() => {
  function approvedUrl(value, origins) {
    if (typeof value !== 'string' || !value || value.length > 8192 || /[\x00-\x20\x7f\\#]/.test(value)) {
      throw new Error('invalid_url');
    }
    let decoded;
    try { decoded = decodeURIComponent(value); } catch (_) { throw new Error('invalid_url'); }
    if (/[\x00-\x1f\x7f\\]/.test(decoded)) throw new Error('invalid_url');
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || !origins.includes(url.origin)) {
      throw new Error('unapproved_origin');
    }
    return url;
  }

  // Serialized by chrome.scripting; self-contained and with no access to config.
  async function readPage(command, origins) {
    const allowed = value => {
      const url = new URL(value);
      if (url.protocol !== 'https:' || url.username || url.password || !origins.includes(url.origin)) {
        throw new Error('unapproved_origin');
      }
      return url;
    };
    const target = allowed(command.url);
    const current = allowed(location.href);
    if (target.origin !== current.origin) throw new Error('wrong_page_origin');
    if (command.operation === 'document') {
      if (target.href !== current.href) throw new Error('wrong_document');
      const body = document.documentElement?.outerHTML || '';
      if (new TextEncoder().encode(body).length > command.max_body_bytes) throw new Error('response_too_large');
      // Successful DOM snapshot, not the document's original network status.
      return {url: current.href, status: 200, body};
    }
    if (command.operation !== 'fetch' || command.method !== 'GET' || command.redirect !== 'error') {
      throw new Error('unsupported_operation');
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(target.href, {
        method: 'GET', redirect: 'error', credentials: 'include', signal: controller.signal,
      });
      if (response.headers?.get('cf-mitigated')?.trim().toLowerCase() === 'challenge') {
        throw new Error('verification_challenge');
      }
      if (allowed(response.url).origin !== target.origin) throw new Error('unexpected_redirect');
      const reader = response.body?.getReader();
      if (!reader) return {url: response.url, status: response.status, body: ''};
      const decoder = new TextDecoder();
      let body = '', size = 0;
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > command.max_body_bytes) {
            await reader.cancel();
            throw new Error('response_too_large');
          }
          body += decoder.decode(chunk.value, {stream: true});
        }
        body += decoder.decode();
        if (new TextEncoder().encode(body).length > command.max_body_bytes) throw new Error('response_too_large');
        return {url: response.url, status: response.status, body};
      } finally { reader.releaseLock(); }
    } finally { clearTimeout(timer); }
  }

  globalThis.AnyplayExecutor = {
    create(config) {
      const origins = [...config.origins];
      const limit = config.max_body_bytes;
      return {
        async run(command, tabId) {
          if (!command || typeof command !== 'object' || !['fetch', 'document'].includes(command.operation)) {
            throw new Error('unsupported_operation');
          }
          const fields = command.operation === 'fetch'
            ? ['operation', 'url', 'method', 'redirect', 'max_body_bytes'] : ['operation', 'url', 'max_body_bytes'];
          if (Object.keys(command).length !== fields.length || !fields.every(key => Object.hasOwn(command, key))
              || command.max_body_bytes !== limit
              || (command.operation === 'fetch' && (command.method !== 'GET' || command.redirect !== 'error'))) {
            throw new Error('invalid_command');
          }
          const target = approvedUrl(command.url, origins);
          const tab = await chrome.tabs.get(tabId);
          if (approvedUrl(tab.url, origins).origin !== target.origin) throw new Error('wrong_tab_origin');
          const results = await chrome.scripting.executeScript({
            target: {tabId}, world: 'MAIN', func: readPage, args: [command, origins],
          });
          const result = results[0]?.result;
          if (!result || Object.keys(result).sort().join(',') !== 'body,status,url'
              || approvedUrl(result.url, origins).origin !== target.origin
              || !Number.isInteger(result.status) || result.status < 100 || result.status > 599
              || typeof result.body !== 'string' || new TextEncoder().encode(result.body).length > limit) {
            throw new Error('invalid_response');
          }
          return result;
        },
      };
    },
  };
})();
