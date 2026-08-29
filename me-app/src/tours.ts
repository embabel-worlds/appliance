/*
 * TOURS, IN THE ME APP — the list, the consent screen, the transcript, and the controls.
 *
 * The kit owns everything that is not this surface: what a step means, whether this surface can run
 * a tour at all, what the user is told before consenting, and the order/pause/resume machine
 * (`@embabel/appliance-kit/tour`). What lives here is the half that is genuinely the Me app's — how
 * to open one of ITS panels, fill one of ITS fields, press one of ITS buttons — plus the DOM.
 *
 * That split is the whole point of the exercise: the console implements the same interface against
 * its own tabs, and one tour file runs on both or is refused by name.
 *
 * THE CONSENT SCREEN IS NOT DECORATION. A tour can arrive from a realm somebody else wrote, so
 * before anything runs, the user sees what it will do — every line of it DERIVED from the file by
 * the kit rather than written by its author. That is the property that makes trying a stranger's
 * tour reasonable, and it is why the vocabulary is closed.
 */

import { $, maybe } from './dom'
import { paint } from './markdown'
import { readDictionary } from './tour-dictionary'
import {
  TourRecorder,
  TourRun,
  describe,
  parseTour,
  refusal,
  type RecordedAction,
  type Tour,
  type TourHost,
  type TourProgress,
  type TourStep,
  type TourStepStatus,
  type TourTarget,
  type WireTour,
} from '@embabel/appliance-kit/tour'
import type { Settings } from './types'

/** Whatever the app already uses to read its settings — supplied by the renderer, not read here. */
type SettingsSource = () => Settings

const POLL_MS = 500

/**
 * The named conditions this surface can answer.
 *
 * Deliberately SHORT. A state is a promise that a tour can wait on something and be told the truth,
 * and a state nobody implements properly is worse than one that does not exist — a tour waits, the
 * probe never becomes true, and the user watches a spinner for ten minutes. Each is one line, and
 * each is observable from the page rather than inferred.
 */
const STATE_PROBES: Record<string, () => boolean> = {
  'connection.ok': () => maybe('conn-pill')?.classList.contains('ok') === true,
  'documents.answered': () => (maybe('ask-answer')?.textContent?.trim().length ?? 0) > 0,
}

export interface TourSurfaceOptions {
  settings: SettingsSource
  /** Switch the app to a tab — the renderer owns tab state, so it hands the act in. */
  showTab: (tab: string) => void
}

export function mountTours(options: TourSurfaceOptions): void {
  new TourSurface(options).mount()
}

class TourSurface {
  private readonly list = $('tour-list')
  private readonly stage = $('tour-stage')
  private readonly transcript = $('tour-transcript')
  private readonly controls = $('tour-controls')
  private readonly status = $('tour-status')
  private running?: TourRun
  private recorder?: TourRecorder

  constructor(private readonly options: TourSurfaceOptions) {}

  mount(): void {
    maybe('tour-refresh')?.addEventListener('click', () => void this.refresh())
    maybe('tour-import')?.addEventListener('click', () => void this.importFile())
    maybe('tour-record')?.addEventListener('click', () => this.toggleRecording())
    void this.refresh()
  }

  // --- The list ------------------------------------------------------------------------------

  private async refresh(): Promise<void> {
    const wire = (await window.me.toursList(this.options.settings())) as WireTour[]
    const dictionary = readDictionary()
    this.list.replaceChildren()

    // GROUPED BY WHERE IT CAME FROM, and the same grouping the console uses — "who is asking me to
    // run this" is the question a reader has about a file they did not write, and a realm's tours
    // should visibly belong to the realm that will take them away again.
    const groups = new Map<string, HTMLElement[]>()
    const into = (heading: string, element: HTMLElement) =>
      groups.set(heading, [...(groups.get(heading) ?? []), element])

    for (const one of wire) {
      // `surface` is the client's own field, in the passthrough map the server never reads — the
      // same arrangement as hints and next-steps. A console tour in the Me app is noise, and a
      // tour that says nothing is meant for both.
      const surface = one.presentation?.['surface']
      if (typeof surface === 'string' && surface !== 'me' && surface !== 'all') continue

      let tour: Tour
      try {
        tour = parseTour(one)
      } catch (e) {
        // A tour this app cannot read is the AUTHOR's problem, and saying which tour and which step
        // is the whole of what they need. Listing it as broken beats omitting it silently.
        into('Will not load', card(one.declaredId ?? one.id, (e as Error).message, []))
        continue
      }
      const synopsis = describe(tour, dictionary)
      const blocked = refusal(tour, dictionary)
      const actions: HTMLElement[] = []
      if (!blocked) actions.push(button('Start', () => void this.start(tour)))
      actions.push(button('Export', () => void this.exportOne(tour)))
      if (tour.deletable) actions.push(button('Delete', () => void this.deleteOne(tour)))
      const heading =
        tour.source ? `From the ${tour.source} realm`
        : tour.userSaved ? 'Yours'
        : 'Shipped with this world'
      into(heading, card(synopsis.title, blocked || synopsis.lines.join('\n'), actions, tour.userSaved))
    }

    // Realms first, then the world's, then the user's own — most-likely-unfamiliar first, since
    // the ones somebody wrote themselves need no introduction.
    const order = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
    for (const heading of order) {
      const title = document.createElement('div')
      title.className = 'subhead'
      title.textContent = heading
      this.list.append(title, ...groups.get(heading)!)
    }

    if (!wire.length) {
      this.list.append(
        note(
          'No tours yet. A realm can ship one, and you can import a file somebody sent you — ' +
            'or record what you just did and hand that back as a draft.',
        ),
      )
    }
  }

  // --- Running one ---------------------------------------------------------------------------

  private async start(tour: Tour): Promise<void> {
    this.transcript.replaceChildren()
    this.stage.hidden = false
    this.say(`**${tour.name}**`)

    const run = new TourRun(tour, {
      host: this.hostFor(tour),
      onProgress: (progress) => this.renderProgress(progress),
    })
    this.running = run
    this.renderControls(run)

    const end = await run.start()
    this.renderProgress(end)
    // Say how it ended IN THE TRANSCRIPT, not only in a status line: somebody who looked away
    // should be able to scroll back and find out what happened without re-running anything.
    if (end.state === 'done') this.say(end.skipped ? `Finished. ${end.skipped} step(s) were already done.` : 'Finished.')
    if (end.state === 'stopped') this.say('Stopped.')
    if (end.state === 'failed') this.say(`Stopped: ${end.error ?? 'something went wrong'}.`)
    this.controls.replaceChildren()
    this.running = undefined
  }

  private renderControls(run: TourRun): void {
    const pause = button('Pause', () => {
      run.pause()
      this.renderControls(run)
    })
    const resume = button('Resume', () => {
      run.resume()
      this.renderControls(run)
    })
    const stop = button('Stop', () => run.stop())
    this.controls.replaceChildren(run.state === 'paused' ? resume : pause, stop)
  }

  private renderProgress(progress: TourProgress): void {
    const total = progress.total
    const at = Math.min(progress.index + 1, total)
    this.status.textContent =
      progress.state === 'running' ? `Step ${at} of ${total}`
      : progress.state === 'pausing' ? `Pausing after step ${at}…`
      : progress.state === 'paused' ? `Paused at step ${at} of ${total}`
      : progress.state === 'done' ? 'Done'
      : progress.state
    if (this.running) this.renderControls(this.running)
  }

  /**
   * The Me app, as a tour host.
   *
   * Every method is one DOM act or one bridge call. Anything longer than that belongs in the kit,
   * where the console gets it too.
   */
  private hostFor(tour: Tour): TourHost {
    const settings = this.options.settings
    return {
      open: (target) => this.options.showTab(target.name),

      set: (target, value) => {
        const field = document.querySelector<HTMLInputElement>(`[data-field="${cssEscape(target.name)}"]`)
        if (!field) throw new Error(`no field ${target.text} on this surface`)
        field.value = value
        // Both events, because a UI may listen for either — and a value set without them is a value
        // the app does not know about, which is the classic way a scripted fill silently does nothing.
        field.dispatchEvent(new Event('input', { bubbles: true }))
        field.dispatchEvent(new Event('change', { bubbles: true }))
      },

      invoke: (target) => {
        const control = document.querySelector<HTMLElement>(`[data-control="${cssEscape(target.name)}"]`)
        if (!control) throw new Error(`no control ${target.text} on this surface`)
        control.click()
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

      say: (markdown) => this.say(markdown),

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

  /** Poll until the state holds or the clock runs out. False on timeout — the runner says so. */
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

  // --- The transcript ------------------------------------------------------------------------

  private say(markdown: string): void {
    const line = document.createElement('div')
    line.className = 'tour-say md'
    // Through the same sanitizing pipeline as every other authored string in this app. A tour can
    // come from a realm somebody else wrote, so this is not a formality.
    paint(line, markdown)
    this.transcript.append(line)
    line.scrollIntoView({ block: 'nearest' })
  }

  private askUser(name: string, question: string): Promise<string | undefined> {
    return new Promise((resolve) => {
      const row = document.createElement('div')
      row.className = 'tour-ask'
      const label = document.createElement('label')
      label.textContent = question
      const input = document.createElement('input')
      input.type = 'text'
      input.name = name
      const ok = button('OK', () => {
        row.replaceChildren(text(`${question} ${input.value}`))
        resolve(input.value)
      })
      const cancel = button('Not now', () => {
        row.replaceChildren(text('Stopped here.'))
        resolve(undefined)
      })
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') ok.click()
      })
      row.append(label, input, ok, cancel)
      this.transcript.append(row)
      input.focus()
    })
  }

  /** The walkthrough half: point, wait, and let them say when they are done — or skip it. */
  private handOver(step: TourStep): Promise<boolean> {
    return new Promise((resolve) => {
      const row = document.createElement('div')
      row.className = 'tour-handover'
      row.append(text(step.hint ?? `Your turn: ${step.target?.text ?? step.verb}`))
      const done = button('Done', () => {
        row.replaceChildren(text('Done.'))
        resolve(true)
      })
      const skip = button('Skip', () => {
        row.replaceChildren(text('Skipped.'))
        resolve(false)
      })
      row.append(done, skip)
      this.transcript.append(row)
      row.scrollIntoView({ block: 'nearest' })
    })
  }

  private showRows(name: string, rows: Record<string, unknown>[]): void {
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
    // A transcript is not a results grid: enough to see that something real came back, and where
    // to go for the rest. Query Studio is one tab away and is built for this.
    for (const row of rows.slice(0, 10)) {
      const tr = document.createElement('tr')
      for (const c of columns) {
        const td = document.createElement('td')
        td.textContent = String(row[c] ?? '')
        tr.append(td)
      }
      table.append(tr)
    }
    this.transcript.append(table)
    if (rows.length > 10) this.say(`…and ${rows.length - 10} more.`)
  }

  // --- Import, export, record -----------------------------------------------------------------

  private async importFile(): Promise<void> {
    const picker = document.createElement('input')
    picker.type = 'file'
    picker.accept = '.yml,.yaml,text/yaml'
    picker.addEventListener('change', async () => {
      const file = picker.files?.[0]
      if (!file) return
      const result = await window.me.tourImport(this.options.settings(), await file.text())
      this.status.textContent =
        result?.ok ? `Imported ${result.tours?.length ?? 0} tour(s).` : `Not imported: ${result?.message}`
      if (result?.ok) void this.refresh()
    })
    picker.click()
  }

  private async exportOne(tour: Tour): Promise<void> {
    const result = await window.me.tourExport(this.options.settings(), tour.id)
    if (!result?.ok) {
      this.status.textContent = `Could not export: ${result?.message}`
      return
    }
    // The clipboard rather than a download: a packaged Electron app's downloads go somewhere the
    // user then has to find, and what they want is to paste this into a message or a repo.
    await navigator.clipboard.writeText(result.yaml)
    this.status.textContent = `Copied “${tour.name}” to the clipboard as YAML.`
  }

  private async deleteOne(tour: Tour): Promise<void> {
    const result = await window.me.tourDelete(this.options.settings(), tour.id)
    this.status.textContent = result?.deleted ? `Deleted “${tour.name}”.` : `Not deleted: ${result?.message ?? 'it is not yours to remove'}`
    void this.refresh()
  }

  /**
   * Recording is the inverse of the resolver: the same annotations that let a tour find a control
   * let a click be named. It is cheap only because the dictionary already exists, which is why it
   * was built last.
   */
  private toggleRecording(): void {
    if (this.recorder) {
      const yaml = this.recorder.toYaml({ id: `Recorded-${Date.now()}`, name: 'Recorded walk' })
      this.recorder = undefined
      document.removeEventListener('click', this.observeClick, true)
      document.removeEventListener('change', this.observeChange, true)
      maybe('tour-record')!.textContent = 'Record'
      void navigator.clipboard.writeText(yaml)
      this.status.textContent = 'Copied the draft to the clipboard. Fill in the TODOs, then import it.'
      return
    }
    this.recorder = new TourRecorder()
    document.addEventListener('click', this.observeClick, true)
    document.addEventListener('change', this.observeChange, true)
    maybe('tour-record')!.textContent = 'Stop recording'
    this.status.textContent = 'Recording. Do the thing you want to teach, then stop.'
  }

  private readonly observeClick = (event: Event) => {
    const target = event.target as HTMLElement | null
    const tab = target?.closest<HTMLElement>('.tab')
    if (tab?.dataset['tab']) return this.record({ verb: 'open', target: `panel.${tab.dataset['tab']}` })
    const control = target?.closest<HTMLElement>('[data-control]')
    if (control?.dataset['control']) this.record({ verb: 'invoke', target: `button.${control.dataset['control']}` })
  }

  private readonly observeChange = (event: Event) => {
    const field = (event.target as HTMLElement | null)?.closest<HTMLInputElement>('[data-field]')
    if (field?.dataset['field']) {
      this.record({ verb: 'set', target: `field.${field.dataset['field']}`, value: field.value })
    }
  }

  private record(action: RecordedAction): void {
    // Never record the tour panel's own controls: a recording of somebody pressing Record is not a
    // tour, and it is the first thing that ends up in every draft otherwise.
    if (action.target?.startsWith('button.tour-')) return
    this.recorder?.observe(action)
    this.status.textContent = `Recording — ${this.recorder?.actions.length ?? 0} step(s).`
  }
}

// --- Small DOM helpers, local because they are this file's shapes -----------------------------

const text = (s: string): HTMLElement => {
  const span = document.createElement('span')
  span.textContent = s
  return span
}

function button(label: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement('button')
  b.type = 'button'
  b.textContent = label
  b.addEventListener('click', onClick)
  return b
}

/** Realms first, the world's next, the user's own last, anything broken at the end. */
function rank(heading: string): number {
  if (heading.startsWith('From the')) return 0
  if (heading === 'Shipped with this world') return 1
  if (heading === 'Yours') return 2
  return 3
}

function note(message: string): HTMLElement {
  const p = document.createElement('p')
  p.className = 'muted'
  p.textContent = message
  return p
}

function card(title: string, body: string, actions: HTMLElement[], mine = false): HTMLElement {
  const wrap = document.createElement('div')
  wrap.className = 'tour-card'
  const h = document.createElement('h3')
  h.textContent = title
  if (mine) {
    const badge = document.createElement('span')
    badge.className = 'pill'
    badge.textContent = 'yours'
    h.append(' ', badge)
  }
  const p = document.createElement('pre')
  p.className = 'tour-synopsis'
  // textContent, not markdown: this is the sentence somebody decides on, and it must be exactly
  // what the kit derived — not something a tour author could style, link or disguise.
  p.textContent = body
  const row = document.createElement('div')
  row.className = 'tour-actions'
  row.append(...actions)
  wrap.append(h, p, row)
  return wrap
}

/** `CSS.escape` where it exists; a conservative filter where it does not. */
const cssEscape = (value: string): string =>
  typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(value) : value.replace(/[^\w-]/g, '')
