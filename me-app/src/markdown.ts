/*
 * Markdown → DOM, for the surfaces the assistant actually writes into: chat
 * replies and document answers. The assistant writes markdown whether or not
 * we render it, so painting it as raw text is a choice to show `**bold**` and
 * `- item` to the user — this file makes the other choice.
 *
 * marked parses; DOMPurify sanitizes; THE POLICY BETWEEN THEM IS THE KIT'S.
 * `MARKDOWN_OPTIONS` and `MARKDOWN_SANITIZE` come from
 * `@embabel/appliance-kit/studio-kit`, because the Worlds console renders the
 * same assistant's prose and a tag one surface allows while the other strips is
 * one answer rendering two ways. What stays here is what only Electron needs.
 *
 * Both libraries are IMPORTED. They used to be vendored under `src/vendor` as
 * browser globals declared `any`; they are dependencies now, bundled into each
 * window by esbuild like everything else, so their own types apply.
 *
 * The policy, in three parts — the first two shared, the third Electron's own:
 *
 *   1. Nothing executable survives. DOMPurify is the boundary, not the parser:
 *      text arriving from a model — or from a document a model quoted — is
 *      untrusted, and marked will happily pass through raw HTML if you let it.
 *   2. Only what written prose needs, and only http(s) hrefs.
 *   3. Links open in the user's browser, never in this window. An Electron
 *      renderer that navigates away from index.html has no way back, so every
 *      anchor is rewritten to go out through the shell. The console's equivalent
 *      is a new tab; the kit holds no opinion, which is why this step is here.
 *
 * Callers can also claim plain-text runs before they are painted. The Ask tab
 * uses that to turn [1] into a citation chip, without this file knowing what a
 * citation is.
 */

import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { MARKDOWN_OPTIONS, MARKDOWN_SANITIZE } from '@embabel/appliance-kit/studio-kit'

/* A FRAGMENT, not the kit's HTML string: the two steps below walk nodes — link
   rewiring needs the anchors, citation chips need the text nodes. The console
   takes the string form of the same policy because React wants one. */
const CLEAN = { ...MARKDOWN_SANITIZE, RETURN_DOM_FRAGMENT: true }

/**
 * Parse [text] as markdown and return a sanitized fragment.
 * @param {string} text
 * @param {(text: string) => Node[]} [decorate] claims plain-text runs
 * @returns {DocumentFragment}
 */
function render(text: string, decorate?: (text: string) => Node[]) {
  const frag = DOMPurify.sanitize(marked.parse(String(text ?? ''), MARKDOWN_OPTIONS) as string, CLEAN) as unknown as DocumentFragment
  for (const a of frag.querySelectorAll('a')) externalize(a)
  if (decorate) decorateText(frag, decorate)
  return frag
}

/** Send this link out through the shell instead of navigating the window. */
function externalize(a: HTMLAnchorElement) {
  const href = a.getAttribute('href')
  if (!href) return
  a.href = '#'
  a.title = href
  a.addEventListener('click', (e: MouseEvent) => {
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
function decorateText(frag: DocumentFragment, decorate: (text: string) => Node[]) {
  const walker = document.createTreeWalker(frag, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      node.parentElement?.closest('code, pre, a') ?
        NodeFilter.FILTER_REJECT
      : NodeFilter.FILTER_ACCEPT,
  })
  const targets: Text[] = []
  for (let n = walker.nextNode() as Text | null; n; n = walker.nextNode() as Text | null) targets.push(n)
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
function paint(el: HTMLElement, text: string, decorate?: (text: string) => Node[]) {
  el.replaceChildren(render(text, decorate))
}

export { render, paint }
