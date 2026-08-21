# realms/

The default mount for **realm checkouts you are working on**. Clone a realm in
here and the appliance can load it in place — no push, no clone by the server,
no cache to defeat:

```bash
git clone https://github.com/embabel/realm-esg.git realms/realm-esg
```

Then, in a world's `config/realms.yml`:

```yaml
- name: esg
  path: /realms/realm-esg
```

The directory is bind-mounted **read-only** at `/realms`. That is deliberate:
you edit here on the host, where your editor, your coding agent and your git
remote already are, and push to your own GitHub from here. The appliance only
reads.

Keep your checkouts somewhere else? Point `EMBABEL_REALMS_DIR` at their parent
in `.env` — or let `./worlds.py` ask you, which is what it does on first run.

Everything in this directory except this file is gitignored: your checkouts are
yours, and they are their own repositories.

One thing to know rather than discover: a realm's declared npm build runs as
part of cloning, so it never fires for a local realm. A declarative realm needs
nothing, and `wasm/handlers.ts` needs nothing either — the appliance compiles it
on load and keeps the bundle in its own storage, never in your checkout. Only a
realm that needs npm must be built here on the host first.
