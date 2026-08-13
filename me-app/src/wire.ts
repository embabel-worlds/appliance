/**
 * What the appliance sends back.
 *
 * These are the shapes this app READS, not the appliance's full API — a
 * deliberately partial description of someone else's JSON. Everything here
 * crosses `window.me`, which is `ipcRenderer.invoke` and therefore `any` at the
 * boundary by construction; writing the fields down is how the renderer stops
 * being `any` all the way down without pretending to a contract the server has
 * never promised.
 *
 * Optional almost throughout, for the same reason: an older appliance answers
 * the same endpoints with fewer fields, and the app already handles that with
 * `??` at every read.
 */

/** Where a cited document actually lives — a local file, or somewhere on the web. */
export interface SourceWhere {
  kind: 'local' | 'url' | string
  label: string
  path?: string
  url?: string
  exists?: boolean
}

/** One citation behind an answer, as the Documents panel renders it. */
export interface DocSource {
  n: number | string
  title?: string
  where: SourceWhere
  createdAt?: string
  ingestedAt?: string
  modifiedAt?: string
  passages?: string[]
}

/** A model the appliance can reach, as the Models panel lists it. */
export interface ModelInfo {
  name: string
  provider?: string
  /** Set on the in-use projection rather than the catalogue. */
  model?: string
  local?: boolean
  purpose?: string
}

/** A realm, installed or merely discoverable. */
export interface RealmSummary {
  name: string
  description?: string
  version?: string
  tags?: string[]
  installed?: boolean
  source?: string
  url?: string
  iconUrl?: string
  metadata?: { stars?: number }
}

/** One realm's outcome from "update all". */
export interface RealmUpdateResult {
  name: string
  status: 'updated' | 'local' | 'error' | string
  summary?: string
  message?: string
}

/** A chat turn as the appliance replays it. */
export interface ChatMessage {
  role: string
  content: string
}

/** One step the document-retrieval loop narrates while it runs. */
export interface AskStep {
  kind?: string
  detail?: string
  query?: string
  [key: string]: unknown
}

/** One label in the virtual-Cypher schema snapshot. */
export interface SchemaLabel {
  label: string
  anchor?: boolean
  description?: string
  /** How many of these the world holds, where the snapshot counted. */
  sampleCount?: number
  /** False when the property list is a sample rather than the whole shape. */
  exhaustive?: boolean
  properties?: Array<{ name: string; type?: string; description?: string; sparse?: boolean }>
}

/** One relationship in that snapshot. */
export interface SchemaRel {
  type: string
  from: string
  to: string
}

/** The reachable shape of the world, as the studio reads it. */
export interface SchemaSnapshot {
  labels: SchemaLabel[]
  relationships: SchemaRel[]
}

/** A saved view, with whatever parameters it declares. */
export interface ViewSpec {
  name: string
  description?: string
  cypher?: string
  group?: string
  /** Where it came from: 'saved' is the user's own, anything else the world's. */
  source?: string
  /** A materialized view is cached; ttl says for how long. */
  materialized?: boolean
  ttl?: string | number
  params?: Record<string, { type?: string; default?: unknown; description?: string }>
}

/** The privacy projection: which models are answering, and where they run. */
export interface ModelsInUse {
  models: ModelInfo[]
  allLocal?: boolean
  cloudProviders?: string[]
}

/** What the Documents panel asks for. */
export interface AskRequest {
  question: string
  dateField?: string
  from?: string
  to?: string
  topK?: number
  history?: Array<{ role: string; content: string }>
}
