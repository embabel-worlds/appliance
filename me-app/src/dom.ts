/**
 * Reaching elements by id, with the types the DOM API cannot give us.
 *
 * `document.getElementById` returns `HTMLElement | null` for every id, because
 * the platform has no idea which element an id names. This app does: `seed` is
 * an input, `mode` is a select, `run` is a button — that knowledge lives in the
 * HTML next door, and every one of these lookups is written by someone who has
 * just read it.
 *
 * So [$] states it. The cast is a real assertion, not a convenience: it says the
 * id exists and is a form control, and if the HTML changes underneath, this lies
 * and the failure lands at the property access rather than here. That is the
 * same failure the app had as JavaScript — `null.value` at runtime — so nothing
 * has been made less safe; the alternative was 119 casts at the call sites,
 * which would bury the interesting ones.
 *
 * Use [maybe] where an element is genuinely optional, and get a null check.
 */

/**
 * The controls this app reaches by id: an HTMLElement plus the form-control
 * properties its call sites read.
 *
 * Declared member by member rather than as
 * `HTMLInputElement & HTMLSelectElement & …`, which looks equivalent and is not:
 * those two disagree about `type` (a `string` against a two-value literal
 * union), so the intersection collapses to `never` and every property access on
 * it fails at once — including `textContent`, which every element has.
 */
export interface Control extends HTMLElement {
  value: string
  checked: boolean
  disabled: boolean
  placeholder: string
  readOnly: boolean
  name: string
  min: string
  max: string
  step: string
  selectedIndex: number
  options: HTMLOptionsCollection
  files: FileList | null
  form: HTMLFormElement | null
  select(): void
  setSelectionRange(start: number, end: number): void
}

/** An element by id, asserted to exist and to be a form control. */
export const $ = (id: string): Control => document.getElementById(id) as unknown as Control

/** An element by id that may legitimately be absent — the caller must check. */
export const maybe = (id: string): HTMLElement | null => document.getElementById(id)
