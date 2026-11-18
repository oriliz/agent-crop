import assert from 'node:assert/strict';
import test from 'node:test';

/* The browser half is a lazy-CJS factory registered with the client module
   loader, so the only way to exercise it in Node is to stand in for the
   loader, `window`, `document` and `react` it expects. */

function installBrowserGlobals() {
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    factory: globalThis.__cropFactory,
  };
  globalThis.document = {
    getElementById() { return null; },
    createElement() { return { id: '', textContent: '', remove() {} }; },
    head: { append() {} },
  };
  globalThis.window = {
    __ModuleLoader__: { load({ factory }) { globalThis.__cropFactory = factory; } },
    setTimeout() { return 0; },
  };
  return () => {
    globalThis.window = previous.window;
    globalThis.document = previous.document;
    globalThis.__cropFactory = previous.factory;
  };
}

function fakeReact() {
  return {
    createElement: () => null,
    useState: (value) => [value, () => {}],
    useEffect: () => {},
    useRef: () => ({ current: null }),
    useCallback: (fn) => fn,
    useMemo: (fn) => fn(),
    Fragment: 'fragment',
  };
}

/* The plugins page keys a row's configuration page as `<bundle>#<rowId>`;
   the form it supplies is the settings form of the profile entry whose id
   equals the row id. Both strings must match dsh/cordis.patch.yml. */
test('client registers a configuration page for the bundle row', async () => {
  const restore = installBrowserGlobals();
  try {
    await import(new URL('../lib/client.js', import.meta.url).href);
    const factory = globalThis.__cropFactory;
    assert.equal(typeof factory, 'function', 'the module loader should receive a factory');
    const module = factory((id) => {
      if (id === 'react') return fakeReact();
      throw new Error(`unexpected require: ${id}`);
    });

    const actions = [];
    const ctx = {
      effect(fn) { fn(); return () => {}; },
      slots: {
        inject(_name, fn) { return fn(); },
        register(options) { actions.push(options); return () => {}; },
      },
    };
    module.apply(ctx);

    const names = actions.map((entry) => entry.name);
    for (const name of ['conversation.view', 'sidebar.panellist', 'main', 'plugins.row.config']) {
      assert.ok(names.includes(name), `missing slot registration: ${name}`);
    }
    const rowPage = actions.find((entry) => entry.name === 'plugins.row.config');
    assert.equal(rowPage.key, 'agent-crop-dsh#agent-crop-dsh');
  } finally {
    restore();
  }
});
