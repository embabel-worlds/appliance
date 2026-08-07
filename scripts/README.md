# scripts/

Things you run to *build* the appliance's own artifacts. Nothing here is needed
to USE the appliance — that is `../install.sh` (or `../me.py`), and it stays at
the root because a one-line installer with a path in it is not a one-line
installer.

| Script | What it builds |
|---|---|
| [`build-me-app.sh`](build-me-app.sh) | The Me sensor app as a real `Embabel Me.app`, and optionally a DMG |

The appliance's own container images are **not** built here — they are built and
published from the `assistant` repo (`docker/build.sh`), and this repo only ever
pulls them by tag. That split is deliberate: this repo is the appliance's
*packaging*, not its source.
