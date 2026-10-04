---
name: scout-agents
description: Find work in a business that nobody has automated — notes written by hand after every failed payment, calls booked every Monday from the same list, a view somebody runs each week and then acts on — and propose the colleagues (agents) to hire for it, with the evidence counted. Prefers adopting an agent an installed realm already proposes over drafting a new one, and also finds agents people would TALK to. Use when the user asks which agents they should have, what to automate next, what their appliance could take off their hands, or wants their business scouted for agents.
---

Point it at a business and it says which colleagues to hire, and what each would have done last
month. Every suggestion stands on evidence you counted, with the query that counted it, so the
user can run it again instead of taking your word.

**Facts are your job, decisions are theirs.** You find the work and count it; they choose who to
hire. Nothing you do here signs an agent, puts one on duty or writes to a business system. The
furthest you go on your own is recording a suggestion, which writes nothing into the world.

## 1. Consent, before you read anything

The evidence lives in the systems the world's realms reach (the CRM, the helpdesk, billing), in
the appliance's own record of what people ran, and sometimes in documents on the user's disk.
Ask which of these to read, in one question:

- **The systems**, by the realm that reaches each: "Odoo, Chatwoot and Lago through their realms".
  `realm_status` lists them; offer what is installed rather than asking them to name it.
- **The appliance's own record**: which saved views people ran, and when. Say what it is — view
  names, who ran them, when — and that it holds about two weeks by default.
- **Documents**, only if they offer: a runbook directory, a wiki export. A procedure written down
  ("every Friday, check the overdue list") is evidence, and it is on their disk, not the appliance.

Read only what they approved. When the scope is obvious — "what should Steward's realm automate?" —
take it and go, without announcing that consent was granted.

## 2. Know who is already here

Before you count anything:

- `list_agents`: every agent in the world. One marked as coming from a realm, off duty, is a
  colleague that realm PROPOSES and nobody has adopted. One already observing or on duty is
  hired: work it covers is not a suggestion at all — say it is already handled, by whom. Read its job, its duties (each `holds` a
  view whose rows are the work) and its routines: these are the candidates to adopt (§4).
- `list_agent_suggestions`: what an earlier scout already suggested. Do not suggest again what the
  user dismissed; refresh an open one by suggesting it under the same name.
- `query_guide`: the live schema, so your evidence queries name labels that exist.

## 3. Count the work nobody has automated

Each signal below is a habit: the same act, by a person, on a rhythm. Count it over a stated period
and keep the query. A signal you cannot count is a hunch; leave it out, or say it is one.

| Signal | What it looks like | How to count it |
|---|---|---|
| **The same note, by hand** | A person writes the same kind of note after the same event: "Payment for INV-… failed yesterday…" | Notes (`AccountNote`, or the CRM's own message type) grouped by author and by their opening words. Thirty-four notes by one clerk that each open "Payment for" is a habit. |
| **The same meeting, on the same day** | Calls booked every Monday from one list: "Overdue invoice check-in: …" | Meetings or activities grouped by title prefix and by the weekday they were booked. The booking day is often only in the description: `split(split(e.description, 'Booked ')[1], ' ')[0]` (there is no `indexOf`). |
| **A view run by hand on a cadence** | Somebody runs the overdue list every Monday, then does something | `MATCH (q:VcQueryLog) WHERE coalesce(q.actor, 'person') = 'person' AND q.viewName IS NOT NULL` grouped by view and weekday (`q.at` is epoch millis). `actor` is `person`, `routine:<name>` or `duty:<agent>/<duty>`: only `person` is work nobody automated. Entries older than the field carry none, hence the `coalesce`. |
| **A condition nobody acts on** | A view or DERIVE label whose rows stay the same week after week | Run it now; compare with what the notes and activities say was done about those rows. |
| **A breach** | Invoices past terms, cases past their SLA, deals past their close date | The realm's own views usually compute these already. Count the rows and how long each has been there. |
| **A procedure in a document** | "Every Friday, check…" | Only from documents they approved. Quote the line and where it is. |
| **Questions about one subject** | People keep asking about one domain, answered from one realm | View runs by `person` whose views all come from one realm (chat runs views as the person). Many scripture questions answered from a bible realm is a colleague to TALK to, not one that works. |

**Automation elsewhere** (a Zapier zap, an n8n flow, a cron script) is not a suggestion: say you
found it, and that importing it is a different job.

## 4. Adopt before drafting

For each habit, ask whether an agent a realm already proposes does that work:

- Its job reads as this work, AND
- a duty's `holds` view returns the rows the habit acts on, or a routine does the act the habit is.

If one does, the suggestion is to **adopt** it — same evidence, plus one sentence in `uncovered`
for any part of the habit it does not do. A realm's Chaser that chases failed payments covers the
clerk's notes; if the clerk also emails the customer and Chaser does not, that is `uncovered`.

Only work no proposed agent covers becomes a new draft. Two colleagues on the same work is worse
than one with a gap you have named.

## 5. Shape each candidate

A suggestion is a JSON object for `suggest_agent`:

```json
{
  "name": "renewals-desk",
  "job": "Books a call with every overdue account the week it goes overdue.",
  "kind": "WORKS",
  "agent": {
    "routing": "Ask me which overdue accounts have a call booked.",
    "duties": [{ "name": "call-booked", "text": "Always — every overdue account has a call booked this week.",
                 "holds": "OverdueWithoutCall", "every": "0 0 9 * * MON" }],
    "authority": { "default": "request" }
  },
  "evidence": [{ "summary": "Calls booked on Mondays titled 'Overdue invoice check-in'", "count": 12,
                 "period": "2026-04-06 to 2026-06-22", "query": "MATCH (b:OdooBook {scope:'all'})-[:HAS_MEETING]->(e:OdooMeeting) …" }],
  "feasibility": "Verbs exist: odoo calendar create. The duty's view OverdueWithoutCall must be written.",
  "rank": 8
}
```

- **kind** is `WORKS` (duties and routines) or `TALKS`. A `TALKS` agent has a `persona` slot, a
  `conversation` with the realms it answers from and the builtins it keeps, and no duties or
  routines; it is drafted able to act on nothing.
- **To adopt**, send `adopt` (the realm agent's name, which is also `name`) and `uncovered`, and no `agent`.
- **feasibility** says what exists and what does not. Check the verbs with `describe_namespace`; a
  duty whose view does not exist yet says so — never pretend the view is there.
- **rank** is value and feasibility together: count times what each instance costs a person,
  discounted by what must be built first. Highest first.
- **authority**: propose `request` for anything that writes. A draft never starts wider than it
  asked, and one with no authority asks first anyway; widening is the sponsor's, at signing.

## 6. Present, then record and draft what they choose

Lead with the verdict — who to hire, in rank order — then each candidate's evidence in numbers,
and for an adoption, what it leaves uncovered. Record each with `suggest_agent`; the console's
Agents window shows them under Suggested colleagues, where the user can draft, adopt or dismiss.

Draft only what the user chose, with `draft_suggested_agent`. The draft is written into the world
unsigned and off duty, and comes back with what each duty would act on today. Say that this is a
look at TODAY, not a replay of last month: the appliance does not yet replay a draft over the past.
Adopting is done from the realm agent's card, by sponsoring it, signing it and putting it on
duty, observing first. Never call `sign_agent` or `set_agent_stage` here.

## What not to do

- Do not suggest an agent for work you did not count. "They probably chase invoices" is not evidence.
- Do not draft a duplicate of a realm's proposed agent. Adopt, and name the gap.
- Do not count work an agent already does: a view run by `routine:` or `duty:` is automated.
- Do not read a system, a log or a directory the user did not approve, or widen because the first
  pass was thin.
- Do not seed data, or suggest seeding it, to make a habit show up.
- Do not sign, raise a stage, or write to a business system. Those are the user's.

## Voice

Output follows `../VOICE.md` (the appliance's `skills/VOICE.md`): verdict first, then evidence;
numbers, not adjectives; no preamble, no postamble, no emoji, no narrating intentions.
