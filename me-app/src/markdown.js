/*
 * Markdown → DOM, for the surfaces the assistant actually writes into: chat
 * replies and document answers. The assistant writes markdown whether or not
 * we render it, so painting it as raw text is a choice to show `**bold**` and
 * `- item` to the user — this file makes the other choice.
 *
 * marked parses; DOMPurify sanitizes; this file is only the policy between
 * them. Both are vendored as plain browser scripts under src/vendor (refreshed
 * by `npm run vendor`), because the renderer has no module system and no Node:
 * index.html loads them with <script> tags like everything else here.
 *
 * The policy, in three parts:
 *
 *   1. Nothing executable survives. DOMPurify is the boundary, not the parser:
 *      text arriving from a model — or from a document a model quoted — is
 *      untrusted, and marked will happily pass through raw HTML if you let it.
 *   2. Links open in the user's browser, never in this window. An Electron
 *      renderer that navigates away from index.html has no way back, so every
 *      anchor is rewritten to go out through the shell.
 *   3. Callers can claim plain-text runs before they are painted. The Ask tab
 *      uses that to turn [1] into a citation chip, without this file knowing
 *      what a citation is.
 */

;(() => {
  const { marked } = window
  const DOMPurify = window.DOMPurify

  marked.setOptions({
    gfm: true, /* tables, strikethrough, autolinks — what the assistant writes */
    breaks: true, /* chat messages use single newlines to mean single newlines */
  })

  /* Allow only what a written answer needs. No forms, no media, no ids — and
     no target/rel games, since every link is rewired below anyway. */
  const CLEAN = {
    ALLOWED_TAGS: [
      'p', 'br', 'hr', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
      'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'table', 'thead', 'tbody', 'tr', 'th', 'td',
    ],
    ALLOWED_ATTR: ['href', 'title', 'start'],
    ALLOWED_URI_REGEXP: /^https?:\/\//i,
    RETURN_DOM_FRAGMENT: true,
  }

  /**
   * Parse [text] as markdown and return a sanitized fragment.
   * @param {string} text
   * @param {(text: string) => Node[]} [decorate] claims plain-text runs
   * @returns {DocumentFragment}
   */
  function render(text, decorate) {
    const frag = DOMPurify.sanitize(marked.parse(String(text ?? '')), CLEAN)
    for (const a of frag.querySelectorAll('a')) externalize(a)
    if (decorate) decorateText(frag, decorate)
    return frag
  }

  /** Send this link out through the shell instead of navigating the window. */
  function externalize(a) {
    const href = a.getAttribute('href')
    if (!href) return
    a.href = '#'
    a.title = href
    a.addEventListener('click', (e) => {
      e.preventDefault()
      window.me?.openExternal?.(href)
    })
  }

  /**
   * Hand every text node to [decorate] and splice back what it returns —
   * except inside code, where a citation marker is just characters someone
   * wrote, and inside links, where replacing the label would break the chip's
   * own click target.
   * @param {DocumentFragment} frag @param {(text: string) => Node[]} decorate
   */
  function decorateText(frag, decorate) {
    const walker = document.createTreeWalker(frag, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) =>
        node.parentElement?.closest('code, pre, a') ?
          NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
    })
    /** @type {Text[]} */
    const targets = []
    for (let n = walker.nextNode(); n; n = walker.nextNode()) targets.push(n)
    for (const node of targets) {
      const replacement = decorate(node.data)
      if (replacement.length === 1 && replacement[0].nodeType === Node.TEXT_NODE) continue
      node.replaceWith(...replacement)
    }
  }

  /**
   * Paint [text] as markdown into [el], replacing what was there.
   * @param {HTMLElement} el @param {string} text
   * @param {(text: string) => Node[]} [decorate]
   */
  function paint(el, text, decorate) {
    el.replaceChildren(render(text, decorate))
  }

  window.markdown = { render, paint }
})()
