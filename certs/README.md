# certs/

Certificate authorities this appliance trusts **in addition to** the public ones.

You need this when your company signs with its own authority — a model gateway
on an internal address, or a network that inspects outbound traffic. Your
machine already trusts that authority, which is why `curl` works from your
terminal; the appliance runs in a container and does not, so the same address
fails from inside it. In the console that reads as *a secure connection could
not be established*.

```bash
embabel trust add ~/Downloads/company-root-ca.crt   # copy it in and restart the app
embabel trust list                                  # what is trusted, with fingerprints
embabel trust remove company-root-ca                # take it back out
```

Give it the **authority** (the root, or the whole chain), not a server's own
certificate. PEM or DER; a file holding several certificates is fine, and
anything in it that is not an authority is left out.

The directory is bind-mounted **read-only** at `/certificates`. When the app
container starts, every `*.crt` file here is imported into its Java trust store
(which every model call goes through) and its system store (which `curl` and
`git` read). What is trusted is exactly what is in this folder: nothing is
recorded in `.env`, and an upgrade leaves the folder alone.

Everything in this directory except this file is gitignored.

Two things to know rather than discover:

- **It needs a restart**, which `embabel trust` does for you. Only the app
  container is recreated; your data is untouched.
- **Anyone who can add a file here decides whose word the appliance takes** for
  every outbound connection. Check the fingerprint `embabel trust add` prints
  against the one your IT team publishes before you rely on it.
