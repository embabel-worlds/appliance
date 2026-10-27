# acceptance/

What must be true of an appliance somebody has just installed, asserted from
outside it: setup closes for good, the operator's account is the way in, nothing
answers without one, the console is served, and the doors the install advertised
are open.

Standard library Python, like the installer beside it. No toolchain to install
before you can run it.

```sh
APPLIANCE_BASE=http://localhost:11043 \
APPLIANCE_USER=you APPLIANCE_PASSWORD=… \
  python3 -m unittest discover -s acceptance -v
```

## It does not start anything

Given an address and an account it tests whatever is there. That is deliberate:
bringing the stack up differs per lane — a hosted runner scales the heavy
services away, a nightly box runs them all, a cold VM installs over the network —
and a suite that starts its own containers can only test the lane it knows how to
start.

`.github/workflows/installer.yml`'s `acceptance` job is the first lane: its own
instance and port block, a headless install, this suite, then `down -v`.

## Variables

| | |
|---|---|
| `APPLIANCE_BASE` | the API (default `http://localhost:11043`) |
| `CONSOLE_BASE` | the console (default: the API's port + 1) |
| `APPLIANCE_USER`, `APPLIANCE_PASSWORD` | the account to sign in with |
| `APPLIANCE_SQL_PORT` | host port of the SQL door — skips that test when unset |
| `COMPOSE_PROJECT` | for the host-binding check — skips it when unset |

## What is deliberately not here

Anything needing a model provider. `POST /setup/provider` makes a real completion
call against the provider, with no stub and no property that skips it, so the
suite that gates a pull request stays keyless — no secret, no cost, nobody else's
uptime in the path. Conversation, embeddings and document conversion belong to the
lane that has a key.

## Adding a test

One promise per test, named as the promise rather than as the endpoint, and a
failure message that says what was expected of the product — not what the
assertion compared. Anything lane-specific skips with a reason instead of
failing: a check that cannot run here has nothing to say about the build.
