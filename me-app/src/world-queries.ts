/*
 * WHAT A WORLD HOLDS ABOUT ITSELF, AS QUERIES. The realms it has and could have, the APIs it skipped,
 * the signal types it has seen, its skills, which agent holds a routine: each is a row in Virtual
 * Cypher, and the kit's RealmCatalog and WorldLists hold the queries over them, shared with the
 * Worlds console. A list here and an answer in chat to the same question therefore come from one
 * place, rather than from a REST payload this app reshapes on its own.
 *
 * Every query goes out as a kit `KgClient.execute` over the bridge, so its declared parameters and
 * their values travel with it, and the main process refuses any route these pages do not use.
 */
import { KgClient, RealmCatalog, WorldLists } from '@embabel/appliance-kit'
import { ipcTransport } from './kit-transport'
import type { Settings } from './types'

export function worldQueries(settings: () => Settings): { catalog: RealmCatalog; lists: WorldLists; kg: KgClient } {
  const kg = new KgClient(ipcTransport(settings))
  const run = (cypher: string, options?: Parameters<KgClient['execute']>[1]) => kg.execute(cypher, options)
  return { catalog: new RealmCatalog(run), lists: new WorldLists(run), kg }
}
