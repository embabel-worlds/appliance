# Usage reporting

*Applies to the Embabel Me **appliance**. Reporting is compiled into the product but runs
only under the `appliance` profile, which the appliance image sets; other deployments
create no reporter, no scheduled task and no endpoint.*

Embabel Me reports a small amount of anonymous usage data every 24 hours. This page is
the complete and authoritative list of what is sent. It is not a summary.

**Your data and your code never leave your installation.** What travels is counts and
versions — how big the graph is, not what is in it; how many documents exist, not their
titles; how many realms are installed, not their names.

You can verify this yourself at any time, from your own instance:

```bash
curl -u <you> http://localhost:4242/api/v1/phone-home           # the exact JSON last sent
curl -u <you> http://localhost:4242/api/v1/phone-home/preview   # what would be sent right now
```

The `json` field in that response is the literal request body, not a re-rendering of it —
compare it against a packet capture and it will match byte for byte.

## Every field

### installation

| Field | Value | Why |
|---|---|---|
| `installationId` | random UUID, minted on first start | Distinguishes one installation from another over time. Random — not derived from your machine, network, hostname, account or install path. |
| `firstSeen` | ISO timestamp | When this installation first reported. |
| `counter` | integer | Increments once per **delivered** report. Distinguishes "ran for a month" from "started thirty times". |
| `sentAt` | ISO timestamp | When this report was assembled. |

### runtime

| Field | Value |
|---|---|
| `version` | application version, e.g. `0.2.0` |
| `packaging` | `docker-compose` or `jar` |
| `uptimeSeconds` | seconds since the process started |

### host

| Field | Value |
|---|---|
| `os` | e.g. `Linux` |
| `arch` | e.g. `aarch64` |
| `processors` | CPU count |
| `totalMemoryMb` | machine memory |
| `jvmMaxHeapMb` | heap available to the app |

### scale

All counts. No names, titles, labels or identifiers.

| Field | Value |
|---|---|
| `users` | number of user accounts |
| `worlds` | number of worlds |
| `realms` | number of installed realms — **the count only; realm names are never sent**, because a realm name routinely names a customer or an internal system |
| `nodes` | nodes in the knowledge graph |
| `relationships` | relationships in the knowledge graph |
| `labels` | number of distinct node labels — the count, never the label names |
| `documents` | ingested documents |
| `chunks` | indexed content chunks |

### activity

Deltas since the previous report, for a fixed list of internal meters. The keys are our
own constants and the values are numbers:

| Key | Counts |
|---|---|
| `http.server.requests` | HTTP requests served |
| `gen_ai.client.operation` | model calls made |
| `codemode.script` | code-mode scripts executed |
| `sandbox.session` | sandbox sessions |
| `kg.ask.refusal` | graph questions the assistant declined |
| `kg.query.warnings` | graph query warnings |

This is an **allowlist**, not "every meter", specifically because meter *tags* elsewhere
in the product are derived from installed namespace names — a realm author's words. An
allowlist means no tag can ever reach the payload.

### modelProviders

Which providers are configured, by name (`openai`, `anthropic`). Presence of a
credential, never any part of its value. No model ids, no prompts, no responses.

## What is never sent

To be explicit, because the absence of a thing is hard to verify from a field list:

- No content of any kind — no message, prompt, model response, document, or code
- No names: no person, organisation, user, world, realm, document, label or file name
- No email addresses, no contacts
- No queries — no Cypher, no search terms
- No credentials, keys or tokens
- No file paths
- No IP address is *sent in the payload*. As with any HTTP request, the collector can
  observe the source address the request arrives from.

## Cadence and mechanics

- First report **10 minutes** after startup, then every **24 hours**. The initial delay
  means a short evaluation or a CI run never reports at all.
- A single `POST` of `application/json` to one host.
- If the collector is unreachable the report is dropped, logged at debug, and the counter
  does not advance. Reporting can never slow down or break your installation.
- Your instance logs one line at startup naming this document.

## Can I turn it off?

There is no configuration flag to disable reporting — it is part of what the product is,
and we would rather be candid about that than ship a switch and hope you do not find the
traffic. What we offer instead is this document, the `/api/v1/phone-home` endpoint that
shows you the exact bytes, and a payload deliberately built so that nothing sensitive
*can* be in it. A test in our build (`PhoneHomeContentTest`) seeds an installation with
distinctive names in every place content could leak and fails the build if any of them
reach the payload.

If your environment forbids outbound telemetry, block the endpoint at your network. We
would rather you do that knowingly than discover it later.

## Questions

Open an issue, or write to us. If something on this page does not match what you observe
from your own instance, that is a bug and we want to hear about it.
