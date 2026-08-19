# site/

The public page for Embabel Me — what it is, what "private" actually means here,
and the one command that installs it.

A single self-contained `index.html`: no build, no dependencies, no external
requests. Open it in a browser, or serve the directory. It wears the same surface
as the Worlds console and the Me app — schematic on black, drifting graph, indigo
as the one signal colour — so the three read as one product.

It lives here rather than in a marketing repo because it advertises
[`install.sh`](../install.sh) and shows [`images/me_electron.png`](../images), and
those should not drift apart from it. Port it into the site of record whenever
that is settled; the whole thing is one file.

## Before it goes live

- The install command points at **raw.githubusercontent.com**, which works today for
  anyone: the repo is public and the installer fetches it anonymously. A prettier
  `get.embabel.com/me` redirecting to the same file is a nice-to-have, not a
  blocker — and the page must not advertise a host that does not resolve, which is
  what it did while that domain was aspirational.
- The image path is repo-relative (`../images/`). Flatten it when deploying, or
  copy the image in.
