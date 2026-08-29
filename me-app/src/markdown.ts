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
import { MARKDOWN_OPTIONS, MARKDOWN_SANITIZE, TOUR_SANITIZE, resolveTourImages } from '@embabel/appliance-kit/studio-kit'

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

/**
 * Paint TOUR narration, which is the one place here that may show an image.
 *
 * Two differences from [paint], and both are about where the picture comes from. The policy is the
 * kit's `TOUR_SANITIZE` — images allowed — and `resolveTourImages` then deletes any whose source is
 * not a rooted path into this appliance, because a tour is a file people exchange and an image on
 * somebody else's host is a beacon that reports when it was opened.
 *
 * Then the surviving ones are FETCHED rather than linked. These windows load over `file://`, so a
 * rooted `src` is a path on the user's disk and not the appliance; and the appliance wants a
 * credential an `<img>` cannot send. [load] does it with the ones the app already holds and returns
 * the bytes. Asynchronous, so the caption paints immediately and each image lands when it arrives —
 * a tour that stalled waiting on a picture would be a worse tour than one without it.
 */
async function paintTour(el: HTMLElement, text: string, load: (path: string) => Promise<string | null>) {
  const frag = DOMPurify.sanitize(marked.parse(String(text ?? ''), MARKDOWN_OPTIONS) as string,
    { ...TOUR_SANITIZE, RETURN_DOM_FRAGMENT: true }) as unknown as DocumentFragment
  for (const a of frag.querySelectorAll('a')) externalize(a)
  resolveTourImages(frag)
  const images = Array.from(frag.querySelectorAll('img'))
  el.replaceChildren(frag)
  await Promise.all(images.map(async (img) => {
    const path = img.getAttribute('src')
    const data = path ? await load(path) : null
    // A picture that could not be fetched leaves no broken icon behind: the words stand alone.
    if (data) img.setAttribute('src', data)
    else img.remove()
  }))
}

export { render, paint, paintTour }
