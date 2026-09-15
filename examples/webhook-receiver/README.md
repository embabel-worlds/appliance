# A watch delivery as a GitHub issue

A reference receiver for a world's webhooks. The world posts a diff of a saved view — the
rows that appeared, changed or went, each with a stable key — and this turns each one into
an issue: opened when the row appears, commented with before and after when it changes,
closed when it goes. One issue per subject, ever, because the key is the identity and a
label carries it.

It is a single file with no dependencies, meant to be copied and changed. The world
decides what changed and what it means; the receiver decides what to do about it, which
is why it is an example here and not a delivery channel in the appliance.

## Run it

```sh
export WORLD_WEBHOOK_SECRET=$(openssl rand -hex 24)   # the watch will sign with this
export GITHUB_TOKEN=github_pat_…                       # issues: write on one repository
export GITHUB_REPO=your-org/your-repo
export RECEIVER_TOKEN=$(openssl rand -hex 24)          # optional: a header the watch must send
python3 receiver.py                                    # listens on :8787
```

The appliance has to reach it. On the same host, `http://host.docker.internal:8787/` from
inside the appliance's container; elsewhere, put it behind whatever you already use to
expose a port.

## Register the watch

```http
POST /api/v1/watches
X-Embabel-Api-Key: emb_…
Content-Type: application/json

{
  "lensId": "SupplierRisk",
  "name": "Supplier risk",
  "cron": "0 */15 * * * *",
  "delivery": {
    "channel": "webhook",
    "url": "http://host.docker.internal:8787/",
    "format": "json",
    "secret": "<WORLD_WEBHOOK_SECRET>",
    "headers": { "X-Receiver-Token": "<RECEIVER_TOKEN>" }
  }
}
```

`headers` go out on every delivery as given. That is what lets a receiver that is an API
rather than an inbox — this one, or a service behind a gateway — demand a credential of
its own. The secret and every header value are write-only: a read of the watch shows a
mask in their place, and sending the mask back on an update keeps what is stored.

Trigger the baseline with `POST /api/v1/watches/{id}/runs`, then change a row the view
returns and run it again. An issue appears with the row, the key, and a link into the world.

## What it checks, in order

1. `X-World-Signature` against the body and the secret. Anything else is a 401.
2. `X-Receiver-Token` against `RECEIVER_TOKEN`, when set. A 401 too.
3. `X-World-Delivery`, the diff id. A redelivery of a diff already applied is answered 200
   and nothing is done twice.

A GitHub error is answered 500, which is the world's cue to try again; the mapping is
idempotent, so a retry after a partial failure completes the rest.

## Try it without GitHub

`../../scripts/check-webhook-receiver.py` drives the mapping with a recorded diff and a fake
GitHub, and is what CI runs. Read it for the exact payload shape.
