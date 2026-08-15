/*
 * Wiring coding agents to this appliance, from the app rather than from a
 * terminal.
 *
 * The Worlds console can only PRINT the command — it is a web page, and a web
 * page cannot run `claude mcp add`. This app can, and setup.py already does
 * exactly that at first run. So the same offer belongs here, for the far more
 * common case: setup ran weeks ago, the scrollback is gone, and a second
 * machine or a second agent needs wiring.
 *
 * THE TOKEN IS NOT OURS TO SHOW, and it is not the user's to keep either.
 *
 * It was minted once during setup, written into the appliance's own data volume,
 * and the server never returns it over HTTP — deliberately. That left the user
 * copying it out of a file inside a Docker volume every time they wired a new
 * agent, which is a hostile way to do a routine thing.
 *
 * So this reads it from the appliance directly. That is not a new privilege: this
 * app already runs `docker` to restart the container, rewrite its compose file and
 * back up its volumes (see appliance.ts, mounts.ts, backup.ts). Reading one value
 * out of a container it can already stop is not an escalation — and it is safer
 * than the alternative, because a token pasted into a text field passes through a
 * clipboard, and a token typed by hand ends up in a screenshot.
 *
 * The token is read at the moment of wiring, handed straight to the client's own
 * config, and never stored, logged or displayed by this app.
 */

import { execFile } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** How an agent is wired, and what to say when it cannot be. */
export interface WireResult {
  ok: boolean
  message: string
}

/** Where setup wrote the token, inside the appliance's data volume. */
const PROVIDER_ENV = '/data/embabel/assistant/admin/providers.env'
const TOKEN_VAR = 'EMBABEL_SETUP_MCP_TOKEN'

/**
 * The appliance's own MCP token, read out of its data volume.
 *
 * Null when the container is not running, when this appliance is not on this machine, or
 * when setup declined MCP and never minted one. Each is a normal answer, not a fault: the
 * caller falls back to asking the operator to paste one.
 */
export function applianceToken(): Promise<string | null> {
  return new Promise((resolve) => {
    execFile(
      'docker',
      ['exec', 'embabel-assistant', 'sh', '-c', `grep '^${TOKEN_VAR}=' ${PROVIDER_ENV} || true`],
      { timeout: 15_000 },
      (error, stdout) => {
        if (error) return resolve(null)
        const line = stdout.split('\n').find((l) => l.startsWith(`${TOKEN_VAR}=`))
        const value = line?.slice(TOKEN_VAR.length + 1).trim()
        resolve(value && value.length > 0 ? value : null)
      },
    )
  })
}

/** Is the Claude Code CLI on PATH? The button says "install it first" rather than failing on click. */
export function claudeAvailable(): boolean {
  const dirs = (process.env['PATH'] ?? '').split(':')
  return dirs.some((d) => d && fs.existsSync(path.join(d, 'claude')))
}

/**
 * `claude mcp add`, at user scope so every project sees the appliance.
 *
 * Runs the CLI rather than writing its config file: the file's location and shape are Claude
 * Code's business, and a config we hand-wrote would be a second implementation of someone
 * else's format, wrong the first time they change it.
 */
export function wireClaudeCode(baseUrl: string, token: string, name = 'embabel'): Promise<WireResult> {
  return new Promise((resolve) => {
    execFile(
      'claude',
      ['mcp', 'add', '--transport', 'http', '--scope', 'user', name, `${baseUrl}/mcp`,
       '--header', `Authorization: Bearer ${token}`],
      { timeout: 60_000 },
      (error, stdout, stderr) => {
        if (!error) return resolve({ ok: true, message: `Claude Code wired as '${name}' — new sessions will see this appliance.` })
        const detail = (stderr || stdout || error.message).trim().slice(0, 200)
        resolve({ ok: false, message: `claude mcp add failed: ${detail}` })
      },
    )
  })
}

/** Where Codex keeps its MCP servers. */
const codexConfig = () => path.join(os.homedir(), '.codex', 'config.toml')

/**
 * Add this appliance to Codex's `config.toml`.
 *
 * Codex has no `mcp add` command, so this writes the file — and therefore does the things a
 * config writer owes the person whose file it is: it refuses rather than overwriting an entry
 * that already exists, it appends rather than rewriting what it does not understand, and it
 * backs up first. A tool that mangles someone's editor config to save them a paste is not a
 * favour.
 */
export function wireCodex(baseUrl: string, token: string, name = 'embabel'): WireResult {
  const file = codexConfig()
  const header = `[mcp_servers.${name}]`
  try {
    const existing = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : ''
    if (existing.includes(header)) {
      return { ok: false, message: `Codex already has an '${name}' server — remove it from ${file} first, or use a different name.` }
    }
    const block = [
      '',
      `# Embabel appliance — added by Embabel Me`,
      header,
      `url = "${baseUrl}/mcp"`,
      `http_headers = { Authorization = "Bearer ${token}" }`,
      '',
    ].join('\n')
    fs.mkdirSync(path.dirname(file), { recursive: true })
    if (existing) fs.copyFileSync(file, `${file}.bak`)
    fs.appendFileSync(file, block)
    return {
      ok: true,
      message: existing
        ? `Added '${name}' to ${file} (previous file saved as config.toml.bak). Restart Codex to pick it up.`
        : `Wrote ${file} with '${name}'. Restart Codex to pick it up.`,
    }
  } catch (e) {
    return { ok: false, message: `Could not write ${file}: ${e instanceof Error ? e.message : String(e)}` }
  }
}

/** The command to paste anywhere else — the token stays a placeholder, because this is displayed. */
export function manualCommand(baseUrl: string): string {
  return `claude mcp add --transport http --scope user embabel ${baseUrl}/mcp \\\n  --header "Authorization: Bearer <your-token>"`
}
