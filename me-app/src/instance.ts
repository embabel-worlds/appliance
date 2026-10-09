/*
 * Which appliance this app is talking to, and the docker names that follow from it.
 *
 * One machine can run several appliances. Each is a compose project called
 * `embabel-<instance>` with its own settings file, and the default instance is
 * called `appliance`, so it keeps the `embabel-appliance` project it always had.
 * These are the same rules the installer follows in embabel_setup/settings.py;
 * every docker call this app makes goes through here, so none of them can fall
 * back to the default appliance by accident, and compose itself is left to the
 * appliance's `embabel compose`.
 *
 * The instance comes from EMBABEL_INSTANCE when the app is started with it, and
 * otherwise from the `instance` the installer wrote into the app's settings.
 */

import { execFile } from 'node:child_process'
import path from 'node:path'

export const DEFAULT_INSTANCE = 'appliance'

/** What compose accepts as a project name; the name is also part of a filename. */
const INSTANCE_NAME = /^[a-z0-9][a-z0-9_-]*$/

let saved = ''

/** Remember the instance from the app's settings, for when the environment has none. */
export function useSavedInstance(name: string | undefined): void {
  saved = (name ?? '').trim()
}

/**
 * The instance this app acts on. A name that cannot be an instance is refused
 * rather than quietly replaced by the default, which would be somebody else's appliance.
 */
export function applianceInstance(env: NodeJS.ProcessEnv = process.env, fallback: string = saved): string {
  const name = (env.EMBABEL_INSTANCE || fallback || DEFAULT_INSTANCE).trim()
  if (!INSTANCE_NAME.test(name) || name.length > 40) {
    throw new Error(`'${name}' cannot be an appliance instance name.`)
  }
  return name
}

/** The compose project, which every container, volume and network name starts with. */
export function composeProject(instance: string = applianceInstance()): string {
  return `embabel-${instance}`
}

/** A named volume as docker knows it: the project, then the key the compose files declare. */
export function volumeName(key: string, instance: string = applianceInstance()): string {
  return `${composeProject(instance)}_${key}`
}

/**
 * The appliance's own `embabel` command, and the arguments that make it act on
 * this instance. Compose actions go through `embabel compose` rather than docker
 * compose directly: the CLI is where the instance's compose files, settings file,
 * port block, memory limits and profiles are worked out, and a container brought
 * up without those limits can take all of Docker's memory.
 */
export function embabelCommand(dir: string, argv: string[], instance: string = applianceInstance()): [string, string[]] {
  return [path.join(dir, 'embabel'), ['--instance', instance, ...argv]]
}

/** `embabel compose --me …` for this instance: the Me mode is the only one this app drives. */
export function composeCommand(dir: string, argv: string[], instance: string = applianceInstance()): [string, string[]] {
  return embabelCommand(dir, ['compose', '--me', ...argv], instance)
}

/** `docker ps` filters for one service of this instance, by its compose labels. */
export function serviceFilter(service: string, instance: string = applianceInstance()): string[] {
  return ['--filter', `label=com.docker.compose.project=${composeProject(instance)}`,
    '--filter', `label=com.docker.compose.service=${service}`]
}

/** This instance's container for a compose service, or '' when there is none. */
export function serviceContainer(service: string, all = false): Promise<string> {
  return new Promise((resolve) => {
    let filters: string[]
    try {
      filters = serviceFilter(service)
    } catch {
      return resolve('')
    }
    execFile('docker', ['ps', ...(all ? ['-a'] : []), ...filters, '--format', '{{.Names}}'],
      { timeout: 15_000 }, (error, stdout) => {
        resolve(error ? '' : (stdout.split('\n').map((l) => l.trim()).find(Boolean) ?? ''))
      })
  })
}
