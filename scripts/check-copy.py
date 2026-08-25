#!/usr/bin/env python3
"""Every say() has a file, and every file has a say().

Copy is the one thing in this repo that CANNOT fail loudly at review time: a
missing block raises only when a user reaches that step of the wizard, and an
orphaned file is a paragraph somebody edited believing it was on screen. Both
are cheap to catch here and expensive to notice in the field.

    python3 scripts/check-copy.py
"""
import os
import pathlib
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY = os.path.join(HERE, "copy")
# EVERY source that can call say(), not just the two that used to. The refactor
# moved most of them into embabel_setup/, and this checker went on scanning the
# old two — reporting "1 block, 5 unused" for copy that is very much in use. A
# checker that quietly stops checking is worse than no checker.
SOURCES = ("setup.py", "embabel", "me.py", "worlds.py") + tuple(
    os.path.join("embabel_setup", f.name)
    for f in sorted(pathlib.Path(os.path.join(HERE, "embabel_setup")).glob("*.py"))
)

used, missing = set(), []
for source in SOURCES:
    with open(os.path.join(HERE, source), encoding="utf-8") as f:
        text = f.read()
    # `say("name")` is one way a block is used; a wizard step naming its own
    # words with `"copy": "name"` is the other. Missing the second would report
    # every step description as an orphan AND let a deleted file through — the
    # failure this checker exists to catch, arriving at a user's screen instead.
    for name in re.findall(r'\bsay\(\s*"([a-z0-9-]+)"|"copy":\s*"([a-z0-9-]+)"', text):
        name = name[0] or name[1]
        used.add(name)
        if not os.path.exists(os.path.join(COPY, f"{name}.txt")):
            missing.append(f"{source}: say(\"{name}\") has no copy/{name}.txt")

# Copy that install.sh carries inline, because it runs before there is a
# checkout to read copy/ from — file name to heredoc delimiter. Checked byte for
# byte below, and never orphans: they are read by the shell script, and
# banner.txt additionally by banner_art().
DUPLICATED = {
    "banner": "ART",
    "docker-required": "DOCKER_REQUIRED",
    "docker-model-runner": "DOCKER_MODEL_RUNNER",
}

on_disk = {f[:-4] for f in os.listdir(COPY) if f.endswith(".txt")}
orphans = sorted(on_disk - used - set(DUPLICATED))

# An interpolated field the caller never passes raises KeyError at the worst
# possible moment, so the placeholders are checked too — against the arguments
# actually written at the call site.
bad_fields = []
for source in SOURCES:
    with open(os.path.join(HERE, source), encoding="utf-8") as f:
        text = f.read()
    for name, args in re.findall(r'\bsay\(\s*"([a-z0-9-]+)"((?:[^()]|\([^()]*\))*)\)', text):
        path = os.path.join(COPY, f"{name}.txt")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            wanted = set(re.findall(r"\{([a-z_]+)\}", f.read()))
        given = set(re.findall(r"(\w+)\s*=", args))
        if wanted - given:
            bad_fields.append(f"copy/{name}.txt wants {sorted(wanted - given)}, "
                              f"call site passes {sorted(given) or 'nothing'}")

# AN UNBALANCED ASIDE eats the rest of the file. `((` with no `))` makes the
# renderer's non-greedy match reach for the next `))` anywhere after it — or
# find none and leave the markers on screen. Both are silent, and both are a
# typo away, so they are counted here instead.
for name in sorted(on_disk):
    with open(os.path.join(COPY, f"{name}.txt"), encoding="utf-8") as f:
        words = f.read()
    if words.count("((") != words.count("))"):
        bad_fields.append(
            f"copy/{name}.txt has {words.count('((')} '((' and {words.count('))')} '))' "
            "— an aside that never closes swallows the text after it")

# Some copy is duplicated into install.sh, which runs before there is a checkout
# to read copy/ from. Duplication is the right call there and a drift risk
# everywhere, so each one is compared byte for byte against its file. The
# heredoc delimiter is the link between the two.
#
# copy/ IS CANONICAL. When these disagree the fix is to carry the file's words
# into install.sh, never to edit install.sh and call it done — an editor works
# in copy/ and would never see the shell script.
with open(os.path.join(HERE, "install.sh"), encoding="utf-8") as f:
    installer = f.read()
for name, delimiter in sorted(DUPLICATED.items()):
    path = os.path.join(COPY, f"{name}.txt")
    if not os.path.exists(path):
        bad_fields.append(f"copy/{name}.txt is missing; install.sh carries a copy of it")
        continue
    # The delimiter, not the whole line: a heredoc may be piped (`| sed …` to
    # indent at render time), and matching the line verbatim made a formatting
    # change look like the copy had vanished.
    marker = f"<<'{delimiter}'"
    if marker not in installer:
        bad_fields.append(f"install.sh no longer carries the {name} heredoc")
        continue
    start = installer.index("\n", installer.index(marker)) + 1
    inline = installer[start:installer.index(f"\n{delimiter}\n", start)]
    with open(path, encoding="utf-8") as f:
        if inline != f.read().rstrip("\n"):
            bad_fields.append(f"install.sh's {name} has drifted from copy/{name}.txt")

for problem in missing + bad_fields:
    print(f"  ✗ {problem}")
for orphan in orphans:
    print(f"  · copy/{orphan}.txt is not used by any say()")

if missing or bad_fields:
    sys.exit(1)
print(f"  ✓ {len(used)} copy block(s), all present and all fields supplied"
      + (f"; {len(orphans)} unused" if orphans else ""))
