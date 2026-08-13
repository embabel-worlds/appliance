/* Themes, applied.
 *
 * The appliance owns them: `*.css` files under its installation, listed by
 * `GET /api/v1/themes` and served by `GET /api/v1/themes/{name}.css`. The Vaadin
 * UI has picked from that list for a while; this is the same list, so a theme
 * chosen in one place is the same theme here.
 *
 * The renderer cannot fetch it. `contextIsolation` is on, there is no HTTP in
 * this process by design, and the credential lives in the main process — so the
 * stylesheet crosses the bridge as text and is painted into the `<style id="theme">`
 * that every window carries. That element sits AFTER the vendored shared
 * stylesheet and BEFORE nothing, so a theme overrides the default palette and
 * Me's own rules still refine the result.
 *
 * A theme only ever redefines `--sb-*` custom properties. It cannot introduce a
 * selector, so a bad or hostile theme file can recolour the app but not
 * restructure it — which is why applying one needs no sanitizing pass.
 */

/* global window, document */

const THEME_STYLE_ID = 'theme'

/** The chosen theme's name, or '' for the appliance default. Lives with the connection. */
function chosenTheme(settings) {
  return (settings && settings.theme) || ''
}

/**
 * Paint a theme. Passing '' clears back to the palette shipped in the shared
 * stylesheet, which is Embabel's own look.
 */
async function applyTheme(settings, name) {
  const el = document.getElementById(THEME_STYLE_ID)
  if (!el) return { ok: false, message: 'no theme element in this window' }
  if (!name) {
    el.textContent = ''
    return { ok: true, message: '' }
  }
  const result = await window.me.themeCss(settings, name)
  // A theme that will not load is not worth a broken window: keep what is
  // painted and say so, rather than clearing to the default and looking fixed.
  if (result.ok) el.textContent = result.css
  return result
}

/**
 * Apply whatever the settings already say, at window start-up, and follow the
 * application menu from then on.
 *
 * The menu lives in the main process and writes the choice to settings.json, so
 * this page's `settings` is stale the moment someone picks one. Mutating the
 * object the page handed us keeps it honest — the connection form saves that
 * same object back, and a stale `theme` on it would quietly undo the choice.
 */
async function restoreTheme(settings) {
  const name = chosenTheme(settings)
  if (name) await applyTheme(settings, name)
  window.me.onThemeChanged((chosen) => {
    settings.theme = chosen
    void applyTheme(settings, chosen)
  })
}

window.meTheme = { applyTheme, restoreTheme, chosenTheme }
