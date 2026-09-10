# The browser harness — driving a world app without a session

The served app sits behind session auth: `/apps/*` 302s to login, so an agent cannot fetch the
page, let alone click it. The harness removes the server from the loop instead of the browser:
the app's real HTML runs in headless Playwright, and the two runtime scripts it loads are
replaced by a stub that serves envelopes captured from live view runs. The app's own bytes are
untouched — what the harness drives is what the user gets.

What this catches that nothing else does: a picker resolving the wrong candidate, a render
loop that drops rows, a hydration step that never fires, a validation path that throws. What
it cannot see — live latency, auth, SSE progress — is covered by the live rungs in
`SKILL.md` §4 (the first-paint budget) and stated at handover.

## 1. Capture, never hand-write

- **The page**: `vibe_app_read` returns the exact served HTML. Save it under
  `tests/fixtures/app.html`.
- **The envelopes**: run every view the app calls, live, with the same arguments its scripts
  send, and save each WHOLE response envelope — `status`, `outcome`, `rows`, `warnings`,
  `cost` — under `tests/fixtures/`. A hand-written fixture tests your imagination; a captured
  one tests the contract. Re-capture whenever a view changes; the harness failing on a
  re-capture is the harness working.

## 2. The stub contract

The app loads two scripts; the harness substitutes both:

- `/api/v1/apps-runtime/v1/embabel.js` → a stub `window.embabel` implementing what apps
  actually use, resolving from fixtures keyed `kind:name:sortedArgsJson`:
  - `views.invoke(name, args, opts)` / `lenses.invoke(name, args, opts)` — return a Promise of
    the WHOLE captured envelope (the real runtime resolves envelopes, not rows). A fixture
    with `status: 'FAILED'` must REJECT, mirroring the runtime.
  - `html.escape`, `format.date` / `format.number` / `format.currency` — real implementations,
    they are trivial.
  - `manifest.read` / `manifest.preflight` / `manifest.ready`, `progress.subscribe` /
    `progress.label`, `cache.invalidate` / `cache.clear`, `EmbabelError` — inert but present.
- `/apps-runtime/gateway.js` → a stub `window.gateway` whose namespaces resolve from the same
  fixture map.

One rule makes the stub honest: **any call with no fixture, and any surface the stub does not
implement, throws loudly.** A silent `undefined` turns a missing capture into a green test.

Substitute by string-replacing the two `<script src>` tags in the saved HTML with local paths
before `page.goto('file://…')` — no route interception, no origin subtleties, and everything
else in the page stays byte-identical.

## 3. What the harness asserts

The floor, from the regressions that motivated it:

- **Render counts**: each list, table and map renders exactly the fixture's row count — not
  "something rendered".
- **Every interaction path**: selection updates the detail pane, toggles toggle (language,
  units, filters), lazy panels hydrate on open, add-flows validate — including the ambiguous
  case: an input matching several candidates must surface the choice, never silently take the
  first.
- **Forced failure**: swap in a `status: 'FAILED'` envelope and an empty-rows-with-warnings
  envelope; the app must show its designed error and empty states, loudly.
- **Zero console errors**: collect `page.on('console')` and `page.on('pageerror')`; any error
  fails the run.

Target shape: dozens of checks, seconds of runtime, no network, no auth — cheap enough to run
after every edit, which is the point.

## 4. A compact skeleton

```js
/* tests/app.spec.js — Playwright. Fixtures are captured envelopes, never hand-written. */
const { test, expect } = require('@playwright/test');
const fs = require('fs'); const path = require('path');

const fixtures = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/envelopes.json')));
const stub = `
  window.embabel = (() => {
    const fx = ${JSON.stringify(fixtures)};
    const key = (kind, id, args) => kind + ':' + id + ':' +
      JSON.stringify(Object.fromEntries(Object.entries(args || {}).sort()));
    const invoke = (kind) => (id, args) => {
      const env = fx[key(kind, id, args)];
      if (!env) return Promise.reject(new Error('NO FIXTURE for ' + key(kind, id, args)));
      return env.status === 'FAILED' ? Promise.reject(Object.assign(new Error(
        (env.error && env.error.message) || 'failed'), env.error)) : Promise.resolve(env);
    };
    const esc = (v) => String(v ?? '').replace(/[&<>'"]/g,
      (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
    return {
      views: { invoke: invoke('view') }, lenses: { invoke: invoke('lens') },
      html: { escape: esc },
      format: { number: (v) => v == null ? '' : Number(v).toLocaleString(),
                date: (v) => v == null ? '' : new Date(v).toLocaleDateString(),
                currency: (v, c) => v == null ? '' : Number(v).toLocaleString(undefined,
                  { style: 'currency', currency: c || 'USD' }) },
      manifest: { read: () => null, preflight: async () => ({ ok: true }),
                  ready: Promise.resolve() },
      progress: { subscribe: () => () => {}, label: () => {} },
      cache: { invalidate: () => {}, clear: () => {} },
      EmbabelError: Error,
    };
  })();
  window.gateway = new Proxy({}, { get: (_, ns) => new Proxy({}, { get: (_, m) =>
    () => Promise.reject(new Error('NO FIXTURE for gateway.' + String(ns) + '.' + String(m))) }) });
`;

function pageHtml() {
  return fs.readFileSync(path.join(__dirname, 'fixtures/app.html'), 'utf8')
    .replace(/<script[^>]*apps-runtime[^>]*><\/script>\s*/g, '')   /* both runtime tags */
    .replace('</head>', `<script>${stub}</script></head>`);
}

test('renders every place and no console errors', async ({ page }) => {
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  const file = path.join(__dirname, 'fixtures/_page.html');
  fs.writeFileSync(file, pageHtml());
  await page.goto('file://' + file);
  const placeRows = fixtures['view:CaPlaceDossier:{}'].rows.length;   /* adapt to the app */
  await expect(page.locator('[data-place]')).toHaveCount(placeRows);
  expect(errors).toEqual([]);
});
```

Adapt the selectors and fixture keys to the app; keep the loud-throw rule and the console
assertion in every spec.

## 5. Handover honesty

The harness green means the page behaves over the captured contract. Say, with the same
prominence: live latency was checked separately against the first-paint budget (the number),
and auth and SSE progress were not exercised by the harness. An authenticated headless route
to the live served app would close that remainder server-side; until it exists, the stub is
the required floor, not the whole truth.
