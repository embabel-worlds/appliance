#!/bin/sh
# Look at every coloured surface without installing, starting, or downloading
# anything. Sources the REAL definitions out of install.sh and setup.py, so what
# you see here is what those files will print — not a mock that can drift.
#
#   ./preview-cli.sh            # as your terminal will render it
#   ./preview-cli.sh | cat      # as a pipe or a log file sees it (no colour)
#   NO_COLOR=1 ./preview-cli.sh # as someone who has opted out sees it
set -eu
cd "$(dirname "$0")/.."

# The colour block spans two `if`s now, so take everything from its comment
# down to the second `fi` — brittle-looking, but it beats a copy of the
# definitions here that could disagree with the ones that ship.
eval "$(awk '/^# FORCE_COLOR matches setup.py/,0' install.sh | awk '/^fi$/{n++} {print} n==2{exit}')"
eval "$(sed -n '/^say()  {/,/^die()  {/p' install.sh)"

printf '\n  %sEmbabel Worlds%s %s— the world your AI acts in%s\n' "$C_BOLD" "$C_RESET" "$C_DIM" "$C_RESET"
printf '  %sA governed, living knowledge graph of your business, derived from the\n' "$C_DIM"
printf '  systems you already run and owned by you. Insight across the whole\n'
printf '  business, in days.%s\n\n' "$C_RESET"
step "Installing into $HOME/embabel/worlds…"
ok "Done. Starting setup — after this, use the 'embabel' command."
printf '  %s!!%s Docker Model Runner looks disabled — embeddings need it.\n' "$C_YELLOW" "$C_RESET"

python3 - <<'PY'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("s", "setup.py")
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)

print(s.banner("first-run setup"))
for title in ("Working on realms", "Wire up coding agents", "Usage reporting"):
    print("\n" + s.heading(title))
print()
s.print_worlds_surfaces("http://localhost:11043")
print(f"  {s.TICK}  docker running")
print(f"  {s.CROSS}  Docker Model Runner (embeddings run locally)")
print(f"     " + s.dim("Enable it in Docker Desktop (Settings → AI)"))
print(f"  {s.MIDDOT}  " + s.dim("no .env yet — this appliance has not been set up here"))
print("\n  " + s.good("All good.") + "   or   " + s.warn("2 problems above") + "\n")
print(f"  colour={s.COLOUR}  (tty={sys.stdout.isatty()}, "
      f"NO_COLOR={'set' if os.environ.get('NO_COLOR') is not None else 'unset'})\n")
PY
