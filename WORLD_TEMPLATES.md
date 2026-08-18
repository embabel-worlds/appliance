# World templates — preconfigured appliances as named artifacts

A world template is a git repo that decides what a new world starts as: which
realms it has, which apps it ships, how it behaves. `./me.py --world <template>`
is the whole install story for a preconfigured appliance — and because a
template is a **named, versioned artifact**, you can audit what a user got, fix
a mistake in one place, and answer "what is this appliance supposed to be?" by
reading a repo. This is deliberately the blessed path over encoding realm lists
into install links: a link's query string is configuration that evaporates; a
template is one you can review, pin, and improve after the fact.

## Using one

```bash
git clone https://github.com/embabel/appliance.git && cd appliance && ./me.py --world arts-world
```

A bare name resolves ONLY inside the embabel org (a short name in a mailed
instruction cannot be squatted); `owner/repo` and full git URLs also work. The
resolved URL is echoed before anything uses it. The template applies when a
world is **first created** — an existing world is never reshaped.

## How inheritance works

Every world resolves through a tier cascade — its own config, then declared
parent templates, then the installation's org config, then the shared
[default-world](https://github.com/embabel/default-world), then the product's
baked-in defaults. First hit wins; anything a tier omits **falls through**. So
a template carries only its *delta*, and improvements to the tiers below reach
its worlds without the template changing.

Realm manifests (`config/realms.yml`) merge across the same tiers, user-first —
a template's realm list rides its tier like its types and apps do, and the
default world's realms arrive regardless.

A template may declare parents of its own (requires an assistant with
[embabel/me#743](https://github.com/embabel/me/pull/743)):

```yaml
# config/world.yml
extends:
  - music-world
  - film-world@v2
```

Parents linearize depth-first in declared order, nearest first, and each
becomes a live tier. `extends` is a human-only field — the assistant's own
tools can never change it.

## Minting a profile template

Three files is a complete template.
[arts-world](https://github.com/embabel/arts-world) is the reference:

```
README.md            what this world is, and the install one-liner
config/realms.yml    ONLY the realms this profile adds
data/README.md       starting content (usually: empty on purpose)
```

The whole ceremony:

```bash
mkdir legal-world && cd legal-world
mkdir -p config data
cat > config/realms.yml <<'YAML'
- name: legal
  repo: https://github.com/embabel/realm-legal.git
  version: "main"
YAML
echo "The world's starting content." > data/README.md
echo "# Legal World — see embabel/appliance WORLD_TEMPLATES.md" > README.md
git init -q && git add -A && git commit -q -m "Legal world template"
gh repo create embabel/legal-world --public --source . --push
```

The shareable instruction is then one line:

```bash
git clone https://github.com/embabel/appliance.git && cd appliance && ./me.py --world legal-world
```

Compose profiles instead of enumerating them: a `media-world` that is
`extends: [music-world, film-world]` plus a README is a real template, and a
customer-specific one is a three-line repo over your catalog.

## What NOT to put in a template

- **Copies of default-world.** Omission *is* inheritance; a copy freezes its
  parent and stops receiving fixes.
- **Restated default realms.** They arrive through the default-world tier.
- **Secrets or keys.** `.env` and the setup wizard own credentials; a template
  that needs a key should ship the realm and let the gaps surface name the
  variable that unlocks it.
- **A user's data.** Templates decide capabilities; `data/` should say why it
  is empty.

## Caveats, honestly

- A **private** template repo clones only when a GitHub token reaches the
  appliance. `setup.py` finds one automatically — any of `GITHUB_TOKEN`,
  `GH_TOKEN` or `GITHUB_PERSONAL_ACCESS_TOKEN` in your shell, else the `gh` CLI's
  own token — and passes it to the containers for that run. Over **HTTPS** and
  **GitHub** only: an `ssh://` or `git@` URL has no key to authenticate with, and
  another host's PAT does not use GitHub's token-as-username convention. The
  token is a whole-appliance credential, not per-template, and a world's own
  credential store is not consulted ([embabel/me#741](https://github.com/embabel/me/issues/741)).
- `extends` parents are pinned in a clone-once cache (`.template-cache`,
  keyed `name@ref`); refresh is explicit, so pin a tag when you need
  reproducibility and use a branch when you want drift.
- Templates using `extends` degrade gracefully on older assistants: the key is
  ignored and the world builds with the shared tiers only.
