/*
 * A TOUR DRIVES THIS APP. IT IS NOT A PAGE ABOUT IT.
 *
 * The first version ran a tour inside the Tours panel: narration accumulated in a transcript and a
 * `run:` printed a table beside it. Everything happened in the one place the user was already
 * looking, so the app never moved — a document with tables, which is the one thing a tour must not
 * be. The console learned this first; this is the same lesson brought back here so the two surfaces
 * behave alike.
 *
 * So the bar lives at the bottom of the WINDOW, outside every panel, and the panels move behind it:
 * `open:` switches the tab and flashes it, `set:` types into the real field, `invoke:` presses the
 * real button. What the user watches is this app working.
 *
 * ONE HONEST DIFFERENCE FROM THE CONSOLE. There, `run: view.X` drives the Views panel and the rows
 * land where somebody would look for them tomorrow. This app has no views panel — Query Studio is a
 * separate window — so the rows are shown in the bar itself, for the current step only and replaced
 * by the next. That is a compact result, not a transcript: nothing accumulates, and there is
 * nothing to scroll back through.
 */

import { maybe } from './dom'
import { paint } from './markdown'
import {
  TourRun,
  type Tour,
  type TourHost,
  type TourProgress,
  type TourStep,
  type TourStepStatus,
  type TourTarget,
} from '@embabel/appliance-kit/tour'
import type { Settings } from './types'

const BEAT_MS = 900
const POLL_MS = 400
/** How long a caption stays up in auto-play, which is off by default. */
const dwellFor = (markdown: string): number =>
  Math.min(11_000, 1_600 + markdown.trim().split(/\s+/).length * 265)

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/**
 * The named conditions this surface can answer.
 *
 * Deliberately short: a state nobody implements properly is worse than one that does not exist,
 * because a tour waits on it and the user watches a spinner.
 */
const STATE_PROBES: Record<string, () => boolean> = {
  'connection.ok': () => maybe('conn-pill')?.classList.contains('ok') === true,
  'documents.answered': () => (maybe('ask-answer')?.textContent?.trim().length ?? 0) > 0,
}

/** Make the thing being acted on unmissable, briefly. */
function flash(element: Element | null): void {
  if (!element) return
  element.classList.add('tour-flash')
  element.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  setTimeout(() => element.classList.remove('tour-flash'), BEAT_MS + 600)
}

export interface TourOverlayOptions {
  settings: () => Settings
  showTab: (tab: string) => void
}

/**
 * The bar, and one run at a time.
 *
 * Built once and reused: a tour that constructed its own chrome each time would flicker, and the
 * bar is also where a finished tour says so.
 */
export class TourOverlay {
  private readonly root = document.createElement('div')
  private readonly title = document.createElement('strong')
  private readonly status = document.createElement('span')
  private readonly progressBar = document.createElement('span')
  private readonly caption = document.createElement('div')
  private readonly rows = document.createElement('div')
  private readonly prompt = document.createElement('div')
  private readonly controls = document.createElement('span')

  private run?: TourRun
  private take = 0
  /** Resolves the current hold — set while a caption is up, cleared when it passes. */
  private skip?: () => void
  /** Re-runs the pointing for the step just performed. */
  private replay?: () => void
  private waiting = false
  private auto = false
  private current?: TourProgress

  constructor(private readonly options: TourOverlayOptions) {
    this.root.className = 'tour-overlay'
    this.root.hidden = true
    const bar = document.createElement('div')
    bar.className = 'tour-overlay-bar'
    const head = document.createElement('div')
    head.className = 'tour-overlay-head'
    this.status.className = 'muted'
    const track = document.createElement('span')
    track.className = 'tour-progress'
    track.append(this.progressBar)
    this.controls.className = 'tour-overlay-controls'
    head.append(this.title, this.status, track, this.controls)
    this.caption.className = 'tour-caption md'
    this.rows.className = 'tour-rows-wrap'
    this.prompt.className = 'tour-prompt'
    bar.append(head, this.caption, this.rows, this.prompt)
    this.root.append(bar)
    document.body.append(this.root)
  }

  /** Run [tour] from the top, replacing whatever was running. */
  start(tour: Tour): void {
    this.take += 1
    const take = this.take
    this.run?.stop()
    this.root.hidden = false
    document.body.classList.add('tour-running')
    this.caption.replaceChildren()
    this.rows.replaceChildren()
    this.prompt.replaceChildren()
    this.title.textContent = tour.name
    this.replay = undefined

    const live = () => take === this.take
    const running = new TourRun(tour, {
      host: this.hostFor(tour),
      // A run that has been told to stop keeps reporting as it unwinds; without this guard its
      // final `stopped` lands after a RESTART has begun and overwrites it, which is exactly what
      // made Restart look broken in the console.
      onProgress: (progress) => { if (live()) this.render(progress) },
    })
    this.run = running
    void running.start().then((end) => {
      if (!live()) return
      this.render(end)
      if (end.state === 'failed') this.say(`Stopped: ${end.error ?? 'something went wrong'}.`)
    })
  }

  close(): void {
    this.take += 1
    this.run?.stop()
    this.run = undefined
    this.root.hidden = true
    document.body.classList.remove('tour-running')
  }

  // --- The host ---------------------------------------------------------------------------------

  private hostFor(tour: Tour): TourHost {
    const settings = this.options.settings
    return {
      open: async (target) => {
        // An APP is a different kind from a panel, and this surface can only get you as far as the
        // Apps panel — it has no route to one app in particular the way the console does. Best
        // effort and honest about it: the panel opens, the narration names the app, and the step
        // after this one is usually a hand-over anyway.
        const tab = target.kind === 'app' ? 'apps' : target.name
        // REFUSE AN UNKNOWN PANEL rather than switching to it. `showTab` hides every panel whose
        // name does not match, so a target this surface does not have would blank the window and
        // leave the user staring at nothing — a tour failing loudly is very much better.
        if (!document.querySelector(`.tabpanel[data-panel="${cssEscape(tab)}"]`)) {
          throw new Error(`no ${target.text} on this surface`)
        }
        this.options.showTab(tab)
        this.pointAgain(() => flash(document.querySelector(`.tab[data-tab="${cssEscape(tab)}"]`)))
        await wait(BEAT_MS)
      },

      set: async (target, value) => {
        const field = document.querySelector<HTMLInputElement>(`[data-field="${cssEscape(target.name)}"]`)
        if (!field) throw new Error(`no field ${target.text} on this surface`)
        this.pointAgain(() => flash(document.querySelector(`[data-field="${cssEscape(target.name)}"]`)))
        // Typed, not pasted: a value that simply appears reads as a screenshot rather than as the
        // thing the user would have done.
        for (let i = 1; i <= value.length; i++) {
          field.value = value.slice(0, i)
          field.dispatchEvent(new Event('input', { bubbles: true }))
          await wait(28)
        }
        field.dispatchEvent(new Event('change', { bubbles: true }))
        await wait(BEAT_MS / 2)
      },

      invoke: async (target) => {
        const control = document.querySelector<HTMLElement>(`[data-control="${cssEscape(target.name)}"]`)
        if (!control) throw new Error(`no control ${target.text} on this surface`)
        this.pointAgain(() => flash(document.querySelector(`[data-control="${cssEscape(target.name)}"]`)))
        await wait(BEAT_MS / 2)
        control.click()
        await wait(BEAT_MS)
      },

      run: async (target, params) => {
        if (target.kind !== 'view') throw new Error(`this surface can only run views, not ${target.kind}`)
        const invocation = await window.me.vcViewInvocation(settings(), target.name, params)
        if (!invocation?.ok) throw new Error(invocation?.message ?? `could not prepare ${target.text}`)
        const result = await window.me.vcExecute(settings(), invocation.cypher)
        if (!result?.ok) throw new Error(result?.message ?? `could not run ${target.text}`)
        this.showRows(target.name, result.rows ?? [])
      },

      waitFor: (target, timeoutMs) => this.until(target, timeoutMs ?? 120_000),
      check: async (target) => this.probe(target),
      say: async (markdown) => {
        this.say(markdown)
        await this.hold(dwellFor(markdown))
      },
      ask: (name, question) => this.askUser(name, question),
      handOver: (step) => this.handOver(step),
      stepStatus: async (index, params) => {
        const answer = await window.me.tourStepStatus(settings(), tour.id, index, params)
        return (answer?.status ?? 'UNKNOWN') as TourStepStatus
      },
    }
  }

  private probe(target: TourTarget): boolean {
    const probe = STATE_PROBES[target.name]
    if (!probe) throw new Error(`this surface cannot answer ${target.text}`)
    return probe()
  }

  private until(target: TourTarget, timeoutMs: number): Promise<boolean> {
    const probe = STATE_PROBES[target.name]
    if (!probe) return Promise.reject(new Error(`this surface cannot answer ${target.text}`))
    return new Promise((resolve) => {
      const deadline = Date.now() + timeoutMs
      const tick = () => {
        if (probe()) return resolve(true)
        if (Date.now() >= deadline) return resolve(false)
        setTimeout(tick, POLL_MS)
      }
      tick()
    })
  }

  /** Wait for Next — or, in auto-play, for [ms]. */
  private hold(ms: number): Promise<void> {
    return new Promise((resolve) => {
      let done = false
      const finish = () => {
        if (done) return
        done = true
        this.skip = undefined
        this.waiting = false
        if (this.current) this.render(this.current)
        resolve()
      }
      this.skip = finish
      if (this.run) this.run.skipHold = finish
      this.waiting = true
      if (this.current) this.render(this.current)
      if (this.auto) setTimeout(() => { if (this.auto) finish() }, ms)
    })
  }

  private pointAgain(fn: () => void): void {
    this.replay = fn
    fn()
    if (this.current) this.render(this.current)
  }

  // --- What the bar shows -----------------------------------------------------------------------

  private say(markdown: string): void {
    // Through the same sanitizing pipeline as every other authored string here. A tour can come
    // from a realm somebody else wrote, so this is not a formality.
    paint(this.caption, markdown)
  }

  private showRows(name: string, rows: Record<string, unknown>[]): void {
    this.rows.replaceChildren()
    if (!rows.length) {
      this.say(`\`${name}\` returned no rows.`)
      return
    }
    const table = document.createElement('table')
    table.className = 'tour-rows'
    const columns = Object.keys(rows[0])
    const head = document.createElement('tr')
    for (const c of columns) {
      const th = document.createElement('th')
      th.textContent = c
      head.append(th)
    }
    table.append(head)
    // The current step's result, not a log: five rows and a count, replaced by the next step.
    for (const row of rows.slice(0, 5)) {
      const tr = document.createElement('tr')
      for (const c of columns) {
        const td = document.createElement('td')
        td.textContent = String(row[c] ?? '')
        tr.append(td)
      }
      table.append(tr)
    }
    this.rows.append(table)
    if (rows.length > 5) {
      const more = document.createElement('p')
      more.className = 'muted'
      more.textContent = `…and ${rows.length - 5} more. Query Studio has the rest.`
      this.rows.append(more)
    }
  }

  private askUser(name: string, question: string): Promise<string | undefined> {
    return new Promise((resolve) => {
      this.caption.replaceChildren()
      const q = document.createElement('p')
      q.className = 'tour-question'
      q.textContent = question
      const row = document.createElement('div')
      row.className = 'row tour-answer'
      const input = document.createElement('input')
      input.type = 'text'
      input.name = name
      const settle = (value: string | undefined) => {
        this.prompt.replaceChildren()
        if (this.current) this.render(this.current)
        resolve(value)
      }
      const ok = button('OK', 'btn primary', () => settle(input.value))
      const no = button('Not now', 'btn ghost tiny', () => settle(undefined))
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') ok.click() })
      row.append(input, ok, no)
      this.prompt.replaceChildren(q, row)
      if (this.current) this.render(this.current)
      input.focus()
    })
  }

  /** The walkthrough half: the tour points and WAITS. */
  private handOver(step: TourStep): Promise<boolean> {
    return new Promise((resolve) => {
      this.caption.replaceChildren()
      const q = document.createElement('p')
      q.className = 'tour-question'
      q.textContent = step.hint ?? `Your turn: ${step.target?.text ?? step.verb}`
      const row = document.createElement('div')
      row.className = 'row tour-answer'
      const settle = (done: boolean) => {
        this.prompt.replaceChildren()
        if (this.current) this.render(this.current)
        resolve(done)
      }
      row.append(button('Done', 'btn primary', () => settle(true)), button('Skip', 'btn ghost tiny', () => settle(false)))
      this.prompt.replaceChildren(q, row)
      if (this.current) this.render(this.current)
    })
  }

  // --- Controls ---------------------------------------------------------------------------------

  private render(progress: TourProgress): void {
    this.current = progress
    const finished = progress.state === 'done' || progress.state === 'stopped' || progress.state === 'failed'
    const at = Math.min(progress.index + 1, progress.total)
    this.status.textContent =
      finished ?
        progress.state === 'done' ?
          progress.skipped ? `Finished — ${progress.skipped} step(s) were already done` : 'Finished'
        : 'Stopped'
      : `Step ${at} of ${progress.total}`
    this.progressBar.style.width = `${(at / Math.max(progress.total, 1)) * 100}%`

    // Weighted, not equalised: the two controls anybody presses are buttons, the rare ones are
    // quiet text, and closing is the one symbol that needs no label. Transport icons were tried on
    // the console and are wrong here — a next-track glyph reads as "skip this step".
    const prompting = this.prompt.childElementCount > 0
    const parts: HTMLElement[] = []
    const extras = document.createElement('span')
    extras.className = 'tour-extras'
    extras.append(link('Restart', 'Start again from the first step', () => {
      const tour = this.run?.tour
      this.skip?.()
      if (tour) this.start(tour)
    }))
    if (this.replay) {
      extras.append(link('Show me again', 'Flash the control this step used', () => this.replay?.()))
    }
    if (!finished) extras.append(this.autoToggle())
    parts.push(extras)
    if (!finished && !prompting) {
      const back = button('‹ Back', 'btn ghost tiny', () => { this.skip?.(); this.run?.back() })
      ;(back as HTMLButtonElement).disabled = progress.index === 0
      parts.push(back)
      const next = button(this.waiting ? 'Next ›' : 'working…', 'btn primary tour-next', () => this.skip?.())
      ;(next as HTMLButtonElement).disabled = !this.waiting
      parts.push(next)
    }
    const close = button('×', 'tour-close', () => this.close())
    close.setAttribute('aria-label', finished ? 'Close the tour' : 'Stop the tour')
    close.title = finished ? 'Close' : 'Stop the tour'
    parts.push(close)
    this.controls.replaceChildren(...parts)
  }

  private autoToggle(): HTMLElement {
    const label = document.createElement('label')
    label.className = 'tour-auto'
    label.title = 'Advance by itself — for showing it to a room'
    const box = document.createElement('input')
    box.type = 'checkbox'
    box.checked = this.auto
    box.addEventListener('change', () => {
      this.auto = box.checked
      if (this.auto) this.skip?.()
    })
    label.append(box, document.createTextNode('auto'))
    return label
  }
}

function button(text: string, className: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement('button')
  b.type = 'button'
  b.className = className
  b.textContent = text
  b.addEventListener('click', onClick)
  return b
}

function link(text: string, title: string, onClick: () => void): HTMLButtonElement {
  const b = button(text, 'tour-link', onClick)
  b.title = title
  return b
}

const cssEscape = (value: string): string =>
  typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(value) : value.replace(/[^\w-]/g, '')
