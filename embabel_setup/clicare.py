"""Looking after it: copies, versions, moving forward, and taking it away.

Everything that answers "what have I got, and how do I not lose it". The destructive
ends of this file — restore, uninstall — confirm first and say exactly what goes.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys

from .cli import (HERE, _emit, _fish_quote, _sample_target, _subparsers, _zsh_quote,
                  current_mode, resolve_instance, resolved_mode, run_setup, s)
# A bug report CAPTURES what these print rather than re-deriving it: the bundle should
# contain what the operator saw, not a second opinion formed a moment later.
from .clirun import cmd_doctor, cmd_status
from .core import prompt
from .settings import embabel_command

def cmd_backup(args) -> int:
    """Everything the appliance knows, copied to the host's own disk.

    Cold: whatever is running stops for the copy and comes back after it. That is
    not caution, it is the only way — Community Neo4j has no online backup, and a
    graph copied live restores as a corrupt graph.
    """
    parent = os.path.abspath(os.path.expanduser(args.directory or s.DEFAULT_BACKUP_DIR))

    if args.list:
        found = s.list_backups(parent)
        if args.json:
            print(json.dumps([{"path": p, **m} for p, m in found]))
            return 0
        if not found:
            print(f"  No backups in {parent}.  `{embabel_command('backup')}` makes one.")
            return 0
        print(f"  Backups in {parent}\n")
        for path, manifest in found:
            when = manifest.get("createdAt", "(undated)")
            size = sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path)
                       if f.endswith(".tgz")) / 1e6
            print(f"    {when}   {size:,.0f} MB   {manifest.get('mode', '?')} mode   {os.path.basename(path)}")
        print(f"\n  {embabel_command('restore')} {found[0][0]}")
        return 0

    try:
        if args.json:
            # The progress narration is for a terminal. The Me app reads stdout as
            # one JSON object, so everything else goes to stderr where its log is.
            with contextlib.redirect_stdout(sys.stderr):
                dest = s.back_up(parent)
        else:
            print(f"  Backing up to {parent}\n")
            dest = s.back_up(parent)
    except s.SetupError as e:
        return _emit(args.json, False, str(e))
    return _emit(args.json, True, f"Backed up to {dest}", path=dest)


def cmd_restore(args) -> int:
    """A backup, back onto this machine — replacing what is here."""
    backup_dir = os.path.abspath(os.path.expanduser(args.directory))
    try:
        manifest = s.inspect_backup(backup_dir)
    except s.SetupError as e:
        return _emit(args.json, False, str(e))

    # WHAT IS ABOUT TO BE LOST is the thing worth printing, and the date of the
    # backup is what somebody actually confirms — a folder name is not a decision.
    if not args.json and not args.yes:
        print(f"  Restoring the backup taken {manifest.get('createdAt', 'at an unknown time')}"
              f" ({manifest.get('mode', '?')} mode).")
        print("  This REPLACES the knowledge graph, the worlds, the documents and this")
        print("  machine's configuration. Everything added since that backup is lost.")
        if input("  Type 'yes' to restore: ").strip().lower() != "yes":
            print("  Nothing was touched.")
            return 1

    try:
        if args.json:
            with contextlib.redirect_stdout(sys.stderr):
                when = s.restore(backup_dir)
        else:
            print()
            when = s.restore(backup_dir)
    except s.SetupError as e:
        return _emit(args.json, False, str(e))
    return _emit(args.json, True,
                 f"Restored the backup from {when} — give the appliance a moment to come up.")


def cmd_version(args) -> int:
    """Which appliance this is, in the four layers that can actually differ.

    Not one number, because there is no one number: the tag is a name, the
    digest is the artifact, the commit is the code inside it, and the checkout
    is everything that lives in files rather than images. An install is only
    reproducible if you can say all four.
    """
    v = s.appliance_versions(args.mode)
    if args.json:
        print(json.dumps(v, indent=2))
        return 0

    src, app, neo, out = v["source"], v["appliance"], v["neo4j"], v["checkout"]
    print("\n  " + s.bold("Appliance") + f"   {s.accent(v['mode'])} mode\n")

    # A DOWNLOADED checkout has no commit to name, and "? on ?" told the one person
    # who most needs to know — somebody testing a branch — the least. It does know
    # which repo and ref it followed, because the install records them.
    if out.get("tarball"):
        print(f"  Checkout    downloaded from {out.get('repo')}@{s.accent(out.get('ref') or 'main')}")
    else:
        print(f"  Checkout    {(out.get('commit') or '?')[:12]} on {out.get('branch') or '?'}"
              + ("  (uncommitted changes)" if out.get("dirty") else "")
              + (f"   {s.dim('follows ' + out['ref'])}" if out.get("ref") and out.get("ref") != "main" else ""))
    print(f"              {s.APPLIANCE_DIR}")

    # Which release it follows, and a legacy pin that outranks it, where somebody comparing
    # two machines would look. The pin is silent unless they disagree — see announce_version_pin.
    print(f"\n  Release     {s.accent(v.get('release') or '?')}   {s.dim('`' + embabel_command('release') + '` for more')}")
    conflict = s.version_pin_conflict(v["mode"])
    if conflict:
        pinned, expected = conflict
        print(f"              {s.warn('!')} .env pins EMBABEL_VERSION={pinned}, which outranks {expected}")

    if app:
        print(f"\n  Server      {src.get('version') or app.get('image', '').rpartition(':')[2] or '?'}")
        print(f"              {app.get('image')}")
        if app.get("digest"):
            print(f"              {app['digest']}")
        if app.get("created"):
            print(f"              image built {app['created'][:19].replace('T', ' ')}Z")
    if src.get("commit"):
        # The abbrev alone means a build from before the full SHA was carried —
        # say so rather than printing seven characters as if they were the answer.
        short = src["commit"] if len(src["commit"]) > 12 else f"{src['commit']} (abbreviated only)"
        print(f"\n  Built from  {short}")
        if src.get("branch"):
            print(f"              on {src['branch']}" + (s.warn("  DIRTY TREE") if src.get("dirty") else ""))
        if src.get("subject"):
            print(f"              \u201c{src['subject']}\u201d")
        if src.get("committed"):
            print(f"              committed {src['committed']}")
        if src.get("dirty"):
            print("              " + s.warn("The build carried uncommitted changes")
                  + " — this commit is")
            print("              where it started, not what is in it.")
    else:
        print("\n  Built from  unknown — could not read the jar (is the image pulled?)")

    if neo.get("image"):
        print(f"\n  Graph       {neo['image']}")
        if neo.get("digest"):
            print(f"              {neo['digest']}")
    elif v.get("engine") == "EMBABEL":
        print("\n  Graph       the Embabel engine, inside the server — its version is the server's")
    elif v.get("engine"):
        print(f"\n  Graph       {v['engine']}")
    print()
    return 0


def cmd_reset_password(args) -> int:
    """Forgot the password. setup.py owns this — it recreates the operator account
    without touching a byte of data, and the reasoning for why that is sound rather
    than a back door is in reset_credentials. A child process so its confirmation
    prompt keeps the terminal it expects."""
    return run_setup("--reset-password")


def cmd_bugreport(args) -> int:
    """Everything a maintainer asks for, in one folder, with the secrets left out.

    doctor and status are CAPTURED rather than re-derived — the bundle should
    contain what the operator was shown on screen, not a second opinion produced
    by a parallel implementation of the same checks.
    """
    # A namespace of their OWN: cmd_version reads .json, and handing it this
    # verb's flags would write a JSON blob into a file called version.txt.
    as_text = argparse.Namespace(mode=getattr(args, "mode", None), json=False)
    captured = {}
    for name, verb in (("doctor.txt", cmd_doctor), ("status.txt", cmd_status), ("version.txt", cmd_version)):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                verb(as_text)
            except Exception as e:  # a broken probe must not cost the whole bundle
                buffer.write(f"\n({verb.__name__} failed: {e})\n")
        captured[name] = buffer.getvalue()

    print("  Collecting — containers, versions, docker's own view, and the logs.")
    if args.all_logs:
        print("  --all-logs: FULL logs. They can carry document titles and names; read")
        print("  the bundle before sending it to anyone.")
    try:
        archive = s.bug_report(args.directory or s.DEFAULT_BACKUP_DIR, captured, everything=args.all_logs)
    except (OSError, s.SetupError) as e:
        print(f"\n  Could not write the report: {e}\n", file=sys.stderr)
        return 1

    print(f"\n  {archive}")
    print(f"  {archive[:-4]}/   (the same thing unpacked — read it before you send it)")
    print("\n  No .env values, no documents, no graph contents are in it.\n")
    return 0


def cmd_upgrade(args) -> int:
    """Onto the latest published build — checkout AND images. Your data is untouched;
    deliberately not `up --fresh`, which is the opposite.

    Both halves, because for a long time this moved only the images while the Me
    app's menu moved both, and the two doors meant different things by the same
    word. setup.py owns it now, so they cannot drift again.
    """
    release = getattr(args, "release", None)
    retry = embabel_command("upgrade", "--release", release) if release else embabel_command("upgrade")
    return _upgrade(args, release, retry)


def _upgrade(args, release: str | None, retry: str) -> int:
    """`upgrade` and `release <value>`: one move, onto the newest build of a release.

    [retry] is how the person asked, because a refused step back answers with the same
    command and --allow-downgrade, and a different command than the one they typed reads
    like a different thing.
    """
    mode = resolved_mode(args.mode)
    try:
        target = s.parse_release(release) if release else s.followed_release()
    except s.SetupError as e:
        print(f"\n  {e}\n", file=sys.stderr)
        return 2
    try:
        following = s.followed_release()
    except s.SetupError:
        # .env names something that does not parse; choosing a release is how that is fixed.
        following = None
    # FLUSHED, because the pull below inherits the terminal and writes to it directly: an
    # unflushed line arrives after the output it was introducing.
    if release and target != following:
        print(f"  Moving the {mode} mode from {s.bold(following or '?')} to {s.bold(target)} "
              f"({s.describe_release(target)}). Your data is untouched.\n", flush=True)
    else:
        print(f"  Upgrading the {mode} mode to the newest {s.bold(target)} build. "
              "Your data is untouched.\n", flush=True)
    # Before the images move: an older `use openai` left a model NAME that newer builds
    # cannot resolve here, and the symptom is documents quietly never indexing again.
    if s.migrate_legacy_embedding_choice():
        print(f"  {s.TICK} Embedding model recorded as the `hosted` role, which newer builds resolve")
        print("  " + s.dim("Same model and the same vectors — nothing is re-embedded.\n"))
    try:
        result = s.upgrade(mode, release=release, allow_downgrade=getattr(args, "allow_downgrade", False))
    except s.DowngradeRefused as e:
        print("\n  " + s.warn("!") + " " + s.downgrade_message(e, retry) + "\n", file=sys.stderr)
        return 1
    except s.SetupError as e:
        print(f"\n  {e}\n", file=sys.stderr)
        return 1
    print()
    for note in result["notes"]:
        print(f"  {note}")
    print(f"\n  `{embabel_command('release')}` says what you follow and what is running now.\n")
    return 0


def cmd_release(args) -> int:
    """Which release this appliance follows and what is running; or, given one, move to it.

    Showing reads the running container's image by id — its labels say what the build
    calls itself, which commit it is and when it was built — so the answer is about what
    is serving, not about what the tag has moved to since.
    """
    if args.value:
        return _upgrade(args, args.value, embabel_command("release", args.value))
    mode = resolved_mode(args.mode)
    report = s.release_report(mode)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print()
    if report["problem"]:
        print(f"  {s.warn('!')} {report['problem']}\n")
        return 1
    where = "" if report["recorded"] else s.dim("  (the default — .env names none)")
    print(f"  Follows     {s.accent(report['release'])}   {report['about']}{where}")
    print(f"              image tag :{report['tag']}")
    pin = report["legacyPin"]
    if pin:
        print(f"              {s.warn('!')} EMBABEL_VERSION={pin['tag']} in {pin['from']} outranks it — the "
              "server runs that tag")
    run = report["running"]
    if run:
        print(f"\n  Running     {run.get('image')}" + s.dim(f"   in {run.get('container')}"))
        if run.get("version"):
            print(f"              version   {run['version']}")
        if run.get("revision"):
            print(f"              revision  {run['revision']}")
        if run.get("created"):
            print(f"              built     {run['created'][:19].replace('T', ' ')}Z")
        if run.get("digest"):
            print(f"              digest    {run['digest']}")
    else:
        print("\n  Running     nothing yet — the first `up` starts the release above")
    print()
    for i, (name, about) in enumerate(s.ABOUT.items()):
        print("  " + ("Releases    " if i == 0 else " " * 12) + s.dim(f"{name:<9} {about}"))
    print(" " * 14 + s.dim("or one build: X.Y.Z, X.Y.Z-rc.N, sha-xxxxxxx"))
    print(f"\n  {embabel_command('release', '<release>')}   follow another, and upgrade onto it\n")
    return 0


def cmd_uninstall(args) -> int:
    return run_setup("--uninstall")


def cmd_instances(args) -> int:
    """Every appliance installed in this checkout, and where each one answers.

    Only interesting once there are two. With one, this prints the same thing
    `status` already told you, which is why the verb is hidden until a second
    instance exists.
    """
    names = s.installed_instances()
    current = s.instance()
    if args.json:
        print(json.dumps([{"name": n, "project": s.compose_project(n),
                           "settings": s.env_file(n), "portBase": s.port_base(n),
                           "ports": s.ports_for(s.port_base(n))} for n in names], indent=2))
        return 0
    print()
    for name in names:
        s.use_instance(name)
        running = sum(1 for c in s.appliance_containers() if c["state"] == "running")
        chosen = bool(args.instance or os.environ.get("EMBABEL_INSTANCE")) or len(names) == 1
        mark = "→" if chosen and name == current else " "
        label = f"{name:<16}"
        print(f"  {s.accent(mark)} " + (s.bold(label) if chosen and name == current else label)
              + s.dim(f" ports {s.port_base(name)}-{s.port_base(name) + s.PORT_BLOCK - 1}")
              + "   " + s.dim(f"{s.env_file(name):<14}") + " "
              + (s.good(f"{running} up") if running else s.dim("stopped")))
    s.use_instance(current)
    if len(names) > 1 and not (args.instance or os.environ.get("EMBABEL_INSTANCE")):
        print("\n  No instance chosen — every other verb will ask for one.")
        print("  embabel --instance <name> <command>, or set EMBABEL_INSTANCE.")
    elif len(names) > 1:
        print(f"\n  Verbs act on '{current}'. Use --instance <name> for another.")
    print()
    return 0


def cmd_trust(args) -> int:
    """Certificate authorities trusted beyond the public ones.

    THE SYMPTOM THIS ANSWERS is an address that works from the terminal and not
    from the appliance. A company's own authority is in the machine's keychain and
    not in the container, so a gateway on an internal address — or ANY address, on
    a network that inspects outbound traffic — is refused before a key is looked at.

    The fingerprint is printed on the way in, every time. Adding an authority
    decides whose word the appliance takes for every connection it makes, and the
    fingerprint is the one thing a person can check against what they were given.
    """
    if args.trust_command in (None, "list"):
        listed = s.trusted()
        if not listed:
            print(f"  No extra certificate authorities.  {embabel_command('trust')} add <file>")
            return 0
        for name, authorities in listed:
            print(f"  {s.bold(name[:-len(s.SUFFIX)])}")
            if authorities:
                s.describe(authorities)
            else:
                print("    " + s.warn("not a certificate authority — the appliance will not gain trust from it"))
        return 0

    if args.trust_command == "add":
        stored, skipped = s.add_trusted(args.file)
        authorities = [authority for _, authority in stored]
        for name, authority in stored:
            print(f"  {s.TICK} Trusting {s.bold(name[:-len(s.SUFFIX)])}")
            s.describe([authority], show_name=False)
        if skipped:
            print("    " + s.dim(f"{skipped} certificate{'s' if skipped > 1 else ''} in the file "
                                 f"{'were' if skipped > 1 else 'was'} left out: not an authority"))
        return 0 if s.apply_trust(current_mode(), authorities) else 1

    if args.trust_command == "remove":
        if not s.remove_trusted(args.name):
            print(f"  Nothing called '{args.name}' is trusted.  {embabel_command('trust')} list")
            return 1
        print(f"  {s.TICK} Removed {args.name}")
        return 0 if s.apply_trust(current_mode(), []) else 1

    print(f"  {embabel_command('trust')} add <file> | list | remove <name>")
    return 1


def cmd_where(args) -> int:
    print(f"  {HERE}")
    return 0


def cmd_completion(args) -> int:
    """Tab completion, GENERATED from the parser rather than written beside it.

    A completion script maintained by hand is a list of verbs that silently stops
    matching the ones that exist — which is worse than no completion, because it
    teaches people the command does not have the verb they just added.
    """
    # IMPORTED HERE, NOT AT THE TOP. cliparser imports this module to name its verbs, so
    # importing it back at module scope is a cycle — and the completion script is the one
    # place that genuinely needs the whole command surface, because it exists to describe
    # it. A deferred import is the smaller price.
    from .cliparser import build_parser

    found = _subparsers(build_parser())
    verbs = sorted(found)

    def flags_for(name: str) -> list[str]:
        """Every long flag EXCEPT the suppressed ones. `--json` on backup and
        restore exists for the Me app, and offering a machine's flag to a person
        pressing tab is how it gets used by one."""
        flags = []
        for action in found[name][0]._actions:
            if action.help is argparse.SUPPRESS:
                continue
            flags += [opt for opt in action.option_strings if opt.startswith("--")]
        return sorted(set(flags))

    def words_for(name: str) -> list[str]:
        """Positional choices and nested verbs — `open console`, `realms link`."""
        words = []
        for action in found[name][0]._actions:
            if not action.option_strings and action.choices:
                words += list(action.choices)
        return sorted(set(words))

    def describe(name: str) -> str:
        return found[name][1] or name

    if args.shell == "bash":
        print("# embabel completion for bash.  Add to ~/.bashrc:")
        print("#   source <(embabel completion bash)")
        print("_embabel() {")
        print(f'  local verbs="{" ".join(verbs)}"')
        print('  if [ "$COMP_CWORD" -eq 1 ]; then')
        print('    COMPREPLY=($(compgen -W "$verbs" -- "${COMP_WORDS[1]}")); return')
        print("  fi")
        print('  case "${COMP_WORDS[1]}" in')
        for verb in verbs:
            print(f'    {verb}) COMPREPLY=($(compgen -W "{" ".join(words_for(verb) + flags_for(verb))}" '
                  '-- "${COMP_WORDS[COMP_CWORD]}")) ;;')
        print("  esac")
        print("}")
        print("complete -F _embabel embabel")
        return 0

    if args.shell == "zsh":
        print("#compdef embabel")
        print("# embabel completion for zsh.  Save as _embabel on your $fpath, or:")
        print("#   embabel completion zsh > \"${fpath[1]}/_embabel\"")
        print("_embabel() {")
        print("  local -a verbs")
        print("  verbs=(")
        for verb in verbs:
            # The one-line help is the whole reason zsh completion is worth having
            # over bash's: it describes the verb rather than only spelling it.
            print(f"    {verb}:{_zsh_quote(describe(verb))}")
        print("  )")
        print("  if (( CURRENT == 2 )); then _describe 'embabel command' verbs; return; fi")
        print("  case ${words[2]} in")
        for verb in verbs:
            options = words_for(verb) + flags_for(verb)
            if options:
                print(f"    {verb}) compadd {' '.join(options)} ;;")
        print("  esac")
        print("}")
        print("_embabel \"$@\"")
        return 0

    print("# embabel completion for fish.  Save as:")
    print("#   embabel completion fish > ~/.config/fish/completions/embabel.fish")
    for verb in verbs:
        help_text = _fish_quote(describe(verb))
        print(f"complete -c embabel -n __fish_use_subcommand -a {verb} -d '{help_text}'")
        for word in words_for(verb):
            print(f"complete -c embabel -n '__fish_seen_subcommand_from {verb}' -a {word}")
        for flag in flags_for(verb):
            print(f"complete -c embabel -n '__fish_seen_subcommand_from {verb}' -l {flag[2:]}")
    return 0


def cmd_graph(args) -> int:
    """The knowledge graph's store. `migrate --to embabel|postgres|oracle` moves the graph — from Neo4j,
    or between the Embabel engine's own stores — and switches the appliance over; see graphmigrate.py."""
    from .graphmigrate import migrate, plan, summary
    if args.graph_command != "migrate":
        print("  embabel graph migrate --to embabel|postgres|oracle   move this appliance's graph to another store")
        return 0
    try:
        move = plan(resolved_mode(args.mode), args.to, args.url, args.username)
        if not _confirm_migration(args.yes, move):
            print("  Nothing was changed.")
            return 1
        report = migrate(move)
    except s.SetupError as e:
        print(f"  {s.warn('!')} {e}")
        return 1
    print(summary(report, move))
    return 0


def _confirm_migration(yes: bool, move) -> bool:
    counts = (f" — {move.counts['nodes']:,} nodes and {move.counts['relationships']:,} relationships —"
              if move.counts else "")
    print(f"  Moving this appliance's graph{counts} {move.describe()}.")
    print("  The app stops while it copies; every value is compared after. The graph it comes from is not")
    print("  changed, and if anything fails the appliance comes back on it as it was.")
    return yes or input("  Type 'yes' to migrate: ").strip().lower() == "yes"
