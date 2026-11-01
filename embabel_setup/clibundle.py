"""`embabel export` and `embabel import`: the commands over bundle.py, bundleimport.py and setupbundle.py.

Both show what they are about to do before doing it — an export prints what goes into the file and what was left out,
with sizes; an import prints what the file holds and what on this appliance it replaces — and both ask before they act
unless told --yes. Neither ever prints a secret: a bundle holds none, and the manifest names what was left out by
kind, never by value.
"""

from __future__ import annotations
import os

from .cli import resolved_mode, s


def _size(n: int | None) -> str:
    n = n or 0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def _describe(manifest: dict) -> None:
    graph = manifest.get("graph") or {}
    if graph:
        print(f"    graph:     {graph.get('nodes', 0):,} nodes, {graph.get('relationships', 0):,} relationships, "
              f"{graph.get('vectors', 0):,} vectors, in {len(graph.get('labelGroups') or {})} label groups")
    skipped = graph.get("notCopied") or {}
    if skipped:
        print(f"    not kept:  {', '.join(f'{n:,} {g.strip(chr(58))}' for g, n in skipped.items())} — the engine holds its "
              "query log only while it runs, so the source's is counted here and not carried")
    data = manifest.get("data") or {}
    if data:
        print(f"    worlds:    {', '.join(data.get('worlds') or []) or 'none'} — {data.get('files', 0):,} files, {_size(data.get('bytes'))}")
    setup = manifest.get("setup") or {}
    for world, items in (setup.get("worlds") or {}).items():
        kinds = ", ".join(f"{len(v)} {k}" for k, v in items.items() if v)
        print(f"    setup:     {world}: {kinds or 'nothing beyond the template'}")
    emb = manifest.get("embedding") or {}
    if emb.get("model"):
        made_by = emb['model'] + (f" ({emb['provider']})" if emb.get('provider') else "") + ("" if emb.get('recorded') else ", as configured")
        print(f"    embedding: {made_by} ({', '.join(str(w) for w in sorted({w for ws in (emb.get('widths') or {}).values() for w in ws})) or 'no vectors'})")
    left = dict(manifest.get("leftOut") or {})
    for group, count in sorted((graph.get("leftOut") or {}).items()):
        left.setdefault("graph", "")
        left["graph"] = (left["graph"] + ", " if left["graph"] else "") + f"{count:,} {group.strip(':')}"
    if left:
        print("    left out:")
        for what, why in left.items():
            print(f"      - {what}: {why}")
    if manifest.get("rawBytes"):
        print(f"    size:      {_size(manifest['rawBytes'])} before compression")


def cmd_export(args) -> int:
    """An appliance's state in one file; see bundle.py (and setupbundle.py for --setup)."""
    mode = resolved_mode(args.mode)
    path = os.path.abspath(os.path.expanduser(args.file))
    if os.path.exists(path) and not args.yes:
        print(f"  {s.warn('!')} {path} already exists; choose another name, or --yes to replace it.")
        return 1

    yes = args.yes

    def confirm(manifest: dict) -> bool:
        print(f"  This bundle ({manifest.get('export', '')}) will hold:")
        _describe(manifest)
        return yes or input(f"  Write it to {path}? Type 'yes': ").strip().lower() == "yes"

    try:
        if args.setup:
            from .setupbundle import export_setup
            manifest = export_setup(path, mode, world=args.world, confirm=confirm)
        else:
            from .bundle import export_bundle
            manifest = export_bundle(path, mode, world=args.world, private=not args.no_private, confirm=confirm)
    except s.SetupError as e:
        print(f"  {s.warn('!')} {e}")
        return 1
    raw, packed = manifest.get("rawBytes") or 0, manifest.get("compressedBytes") or 0
    ratio = f", {packed / raw:.0%} of {_size(raw)} raw" if raw else ""
    print(f"  {s.TICK} Wrote {path}: {_size(packed)}{ratio}.")
    print(f"    Take it into another appliance with `{s.embabel_command('import')} {os.path.basename(path)}`.")
    return 0


def cmd_import(args) -> int:
    """A bundle taken into this appliance; see bundleimport.py (and setupbundle.py for a setup bundle)."""
    mode = resolved_mode(args.mode)
    path = os.path.abspath(os.path.expanduser(args.file))
    if not os.path.isfile(path):
        print(f"  {s.warn('!')} {path} is not a file.")
        return 1
    from .bundleimport import read_manifest
    try:
        manifest = read_manifest(path)
        setup_only = manifest.get("kind") == "setup"

        yes = args.yes

        def confirm(m: dict) -> bool:
            print(f"  {os.path.basename(path)} was exported {m.get('createdAt', '')} from a "
                  f"{(m.get('appliance') or {}).get('store', '?')} appliance and holds:")
            _describe(m)
            if setup_only:
                print("  It adds to this appliance's worlds: realms are installed, files are written beside what is there.")
            else:
                print("  It REPLACES this appliance's graph and worlds. What is here now is set aside, not deleted,")
                print("  and comes back if the import does not verify. This appliance's settings and keys are kept.")
            return yes or input("  Type 'yes' to import: ").strip().lower() == "yes"

        if setup_only:
            from .setupbundle import import_setup
            report = import_setup(path, mode, world=args.world, confirm=confirm)
            _print_setup_report(report)
            return 0 if not report.get("failed") else 1
        from .bundleimport import import_bundle
        report = import_bundle(path, mode, login=args.login, confirm=confirm)
    except s.SetupError as e:
        print(f"  {s.warn('!')} {e}")
        return 1
    print(f"  {s.TICK} Imported and verified: {report.get('nodes', 0):,} nodes, {report.get('relationships', 0):,} relationships, "
          f"{report.get('vectors', 0):,} vectors, every value compared with the bundle.")
    print(f"    What was here before is kept as {report.get('setAside')} on the data volume.")
    if report.get("setup"):
        _print_setup_report(report["setup"])
    if report.get("login"):
        print(f"    {report['login']}.")
    elif not args.login:
        print(f"    The bundle carries no passwords: `{s.embabel_command('import')} … --login USER` sets one, or "
              f"`{s.embabel_command('reset-password')}`.")
    if report.get("embedding"):
        print(f"  {s.warn('!')} {report['embedding']}")
    return 0


def _print_setup_report(report: dict) -> None:
    for line in report.get("installed") or []:
        print(f"    {s.TICK} {line}")
    for line in report.get("failed") or []:
        print(f"    {s.warn('!')} {line}")
    for line in report.get("notes") or []:
        print(f"    - {line}")
