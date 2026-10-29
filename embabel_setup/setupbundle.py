"""`embabel export --setup` and the import of what it writes: how a person has set their world up, without its data.

WHAT SETUP IS. What somebody chose or built, as opposed to what their world learned:
  realms        as REFERENCES — the repository and ref each came from, installed again on the other side through the
                server's own install (`POST /realms`, the route `install_realm` takes), so admission runs there too.
                A realm that exists only here — written in this world (`config/realms/<name>`, drafts included) —
                travels as its files; one linked from a folder on this machine (`/realms/...`) travels as its path
                and installs only where the same folder is mounted, which the import says
  files         what this world's config/ holds that its template did not (the world is a clone of its template, so
                git says which files were written here), and data/apps — the apps built here: lenses and views as
                files, saved views, actions and their handlers, skills, agents, schedules (cron), commands,
                personalities, prompt overrides, focuses, goals, tours, connections, and world.yml — model role
                assignments, the active personality, notification routing and the rest of the world's settings
  graph-held    saved lenses and watches live in the graph, so they travel as JSON read from the API and are
                saved again through it
  themes        custom themes (admin/themes/*.css), which every world on an appliance shares
  embedding     the model this appliance embeds with — recorded and compared, never switched: changing it means
                re-embedding everything, which an import should not start behind somebody's back

WHAT IT NEVER IS. A secret — the same two layers as a full bundle (bundlesecrets.py): files named like credentials
are never selected, and a selected file whose content holds one is LEFT OUT and named in the manifest (a connection
with a key in a header is the usual one), never sent. Nor anything the world learned: the persona the server
writes from email and calendar, caches, the realm cache, chat, memories, documents.

HOW THE CONSOLE IS ARRANGED. The chosen theme and its rotation, the wallpaper, the window style and layout, pinned apps
and the dock's realm stacks are the person's console preferences, which the server keeps in the world as
config/console.json (GET and PUT /profile/console), so they travel as one of the world's own files. What the console
still keeps only in the browser (BROWSER_ONLY) no server has to export, and the manifest says so.

IMPORT ADDS; IT DOES NOT REPLACE. A setup goes into one world on a running appliance, beside what is there: a file it
replaces is kept under .setup-import-<stamp>, realms already installed are left alone, and the report says, item by
item, what was installed and what failed.
"""

from __future__ import annotations
import datetime
import json
import os
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request

from . import bundlesecrets
from .bundle import FORMAT, STAGING, WORLD_BASE, BundleError, _helper, _write_text, stream_tar
from .core import MODE_SERVICE
from .dockerlib import _compose, _docker, container_base_url, find_mode_container, service_container
from .samples import admin_username, sample_credential
from . import graphstores as stores
from .settings import embabel_command, env_file_value, graph_engine
from .versions import mode_image

THEMES = "admin/themes"
# Inside a world, never setup: written by the server from the person's own data, or rebuilt by it.
NOT_SETUP = (
    "config/.embabel/persona.md",       # a profile of the person, written from their email and calendar
    "config/realms.yml", "config/packs.yml",  # realms travel as references and are installed, not copied
    "config/skills/.cache/*", "config/actions/.embabel/*", "config/apis/specs/*", "*/.gitkeep", "*/.git/*", ".git/*", "*/.embabel/codemode/*",
)
# Where each kind of setup lives in a world, for the manifest and the report; first match wins.
KINDS = (
    ("config/realms/", "local realms"), ("data/apps/", "apps"), ("config/apps/", "apps"),
    ("config/lenses/", "lens files"), ("config/views/", "views"), ("config/tours/", "tours"),
    ("config/actions/", "actions"), ("config/skills/", "skills"), ("config/agents/", "agents"),
    ("config/cron/", "schedules"), ("config/commands/", "commands"),
    ("config/behaviour/", "personalities and prompts"), ("config/.embabel/prompts/", "personalities and prompts"),
    ("config/world.yml", "preferences"), ("config/console.json", "console preferences"), ("config/mcp/", "connections"), ("config/apis/", "connections"),
    ("config/channels/", "connections"), ("config/sql/", "connections"), ("config/webhooks/", "connections"),
)
BROWSER_ONLY = ("Apps and Realm store view options", "sounds on or off", "tours completed")
CONSOLE = "config/console.json"


# ── the API, as the person whose setup it is ──────────────────────────────────────────────────────────


class Api:
    """The running appliance's API, signed in as one user (Basic, as `embabel sample` does; nothing is stored)."""

    def __init__(self, mode: str):
        container = find_mode_container(mode)
        self.base = container_base_url(container) if container else None
        if not self.base:
            raise BundleError(f"The appliance is not up; a setup is read and written through its API. `{embabel_command('up')}` first.")
        self.user = os.environ.get("EMBABEL_USER") or admin_username(container)
        self.auth = sample_credential(container)

    def call(self, method: str, path: str, payload=None, ok_missing: bool = False):
        request = urllib.request.Request(f"{self.base}/api/v1{path}", method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None)
        request.add_header("Authorization", self.auth)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                body = response.read()
                return json.loads(body or "null") if "json" in (response.headers.get("Content-Type") or "json") else body.decode()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise BundleError(f"The appliance did not accept the password for {self.user}.")
            if ok_missing and e.code == 404:
                return None
            raise ApiRefused(e.code, e.read().decode("utf-8", "replace")[:300])
        except urllib.error.URLError as e:
            raise BundleError(f"The appliance's API did not answer on {self.base} ({e.reason}).")


class ApiRefused(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"{status}: {body}")
        self.status = status


def _world_dir(image: str, user: str, world: str | None) -> str:
    """users/<user>/<world>: the one named, or the user's only world, or `default`."""
    if world:
        return f"users/{world}" if "/" in world else f"users/{user}/{world}"
    run = _helper(image, f"ls -1 {WORLD_BASE}/users/{shlex.quote(user)} 2>/dev/null")
    names = [n for n in run.stdout.decode().split() if not n.startswith(".")]
    return f"users/{user}/{names[0] if len(names) == 1 else 'default'}"


def _kind(path: str) -> str:
    return next((kind for prefix, kind in KINDS if path.startswith(prefix)), "other settings")


# ── export ──────────────────────────────────────────────────────────────────────────────────────────


def export_setup(path: str, mode: str, world: str | None = None, confirm=None) -> dict:
    """The setup of one world (default: the signed-in user's) in a bundle at [path]; returns its manifest."""
    image = mode_image(mode)
    api = Api(mode)
    where = _world_dir(image, api.user, world)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = f"{STAGING}/setup-{stamp}"
    _helper(image, f"mkdir -p {staging}/setup")
    try:
        files, withheld = _setup_files(image, where, staging)
        themes = _themes(image, staging)
        realms = _realms(api, image, where)
        lenses, watches = _graph_held(api, _shipped_lenses(image, where))
        lenses = _without_secrets(lenses, "lens", "id", withheld)
        watches = _without_secrets(watches, "watch", "name", withheld)
        for name, value in (("realms", realms), ("lenses", lenses), ("watches", watches)):
            _write_text(image, f"{staging}/setup/{name}.json", json.dumps(value, indent=2))
        manifest = _setup_manifest(where, files, themes, realms, lenses, watches, withheld)
        manifest["rawBytes"] += sum(len(json.dumps(v, indent=2)) for v in (realms, lenses, watches))
        _write_text(image, f"{staging}/manifest.json", json.dumps(manifest, indent=2))
        if confirm and not confirm(manifest):
            raise BundleError("Nothing was written.")
        stream_tar(image, f"tar -cf - -C {staging} manifest.json setup "
                          # tar applies every --transform to every member, so each one matches only its own: world files start
                          # config/ or data/, themes are bare *.css, and the manifest and setup/*.json match neither
                          f"--transform 's,^\\(config\\|data\\)/,setup/world/\\1/,' --transform 's,^\\([^/]*\\.css\\)$,setup/themes/\\1,' "
                          f"-C {WORLD_BASE}/{where} --verbatim-files-from -T {staging}/files.list "
                          f"-C {WORLD_BASE}/{THEMES} --verbatim-files-from -T {staging}/themes.list",
                   path)
        manifest["compressedBytes"] = os.path.getsize(path)
        return manifest
    finally:
        _helper(image, f"rm -rf {staging}")


def _setup_files(image: str, where: str, staging: str) -> tuple[list[dict], list[str]]:
    """The world's own files: what git says was written or changed since the template was cloned (every config file,
    when the world is not a clone), and data/apps. Secrets by name never listed; by content, listed as withheld."""
    excludes = " ".join(f"! -path {shlex.quote('./' + p)}" for p in NOT_SETUP) + " " + \
        " ".join(f"! -name {shlex.quote(n)}" for n in bundlesecrets.SECRET_NAMES)
    script = f"""
        cd {WORLD_BASE}/{where} || exit 3
        {{ if [ -d .git ]; then git -c safe.directory='*' ls-files -m -o -- config | sed 's,^,./,'; else find ./config -type f; fi
           [ -d data/apps ] && find ./data/apps -type f; }} | sort -u | while IFS= read -r f; do [ -f "$f" ] && echo "$f"; done > {staging}/all.list
        find . -type f {excludes} | sort > {staging}/allowed.list
        comm -12 {staging}/all.list {staging}/allowed.list | sed 's,^\\./,,' > {staging}/candidates.list
    """
    run = _helper(image, script)
    if run.returncode != 0:
        raise BundleError(f"No world at {where} — name one with --world USER/WORLD. "
                          f"({run.stderr.decode(errors='replace').strip()[-200:]})")
    scan = _helper(image, bundlesecrets.scan_script(f"{WORLD_BASE}/{where}", f"{staging}/candidates.list"))
    withheld = {line.split("\t", 1)[1]: line.split("\t", 1)[0] for line in scan.stdout.decode(errors="replace").splitlines() if "\t" in line}
    keep = f"grep -vxF -f {staging}/withheld.list {staging}/candidates.list" if withheld else f"cat {staging}/candidates.list"
    _write_text(image, f"{staging}/withheld.list", "\n".join(withheld) + "\n")
    listed = _helper(image, f"cd {WORLD_BASE}/{where} && {keep} > {staging}/files.list; "
                            f"while IFS= read -r f; do printf '%s\\t%s\\n' \"$(stat -c %s \"$f\")\" \"$f\"; done < {staging}/files.list")
    files = [{"path": p, "bytes": int(n), "kind": _kind(p)}
             for n, _, p in (line.partition("\t") for line in listed.stdout.decode(errors="replace").splitlines()) if p]
    return files, [f"{p} — holds {what}; set it up again on the other appliance" for p, what in withheld.items()]


def _themes(image: str, staging: str) -> list[str]:
    run = _helper(image, f"cd {WORLD_BASE}/{THEMES} 2>/dev/null && ls -1 *.css 2>/dev/null > {staging}/themes.list; "
                         f"touch {staging}/themes.list; cat {staging}/themes.list")
    return run.stdout.decode().split()


def _realms(api: Api, image: str, where: str) -> list[dict]:
    """Each realm this world has loaded, as what installs it again. A realm cloned from a repository is read from its
    checkout in the realm cache — the remote it came from (any credential in the URL removed), the ref it tracks and
    the commit it is at; one written in this world is its files (already listed); anything else was linked from a
    folder on this machine, and goes as its name, with a note that it travels only where that folder is mounted."""
    rows = api.call("GET", "/realms") or []
    run = _helper(image, f"""
        cd {WORLD_BASE}/.realm-cache 2>/dev/null && for d in *@*; do
          printf 'git\t%s\t%s\t%s\n' "$d" "$(git -c safe.directory='*' -C "$d" remote get-url origin 2>/dev/null)" \
            "$(git -c safe.directory='*' -C "$d" rev-parse HEAD 2>/dev/null)"; done
        cd {WORLD_BASE}/{where}/config/realms 2>/dev/null && for d in *; do [ -d "$d" ] && printf 'world\t%s\n' "$d"; done; true""")
    git, local = {}, set()
    for line in run.stdout.decode(errors="replace").splitlines():
        kind, *rest = line.split("\t")
        if kind == "git" and len(rest) == 3:
            name, _, ref = rest[0].partition("@")
            git.setdefault(name, {"repo": _without_userinfo(rest[1]), "ref": ref, "commit": rest[2] or None})
        elif kind == "world" and rest:
            local.add(rest[0])
    # A realm's checkout is named for how it was installed (realm-chess) and its listing for its realm.yml (chess),
    # so the remote it was cloned from is the join; the name is the fallback for a listing that has no source.
    by_remote = {entry["repo"]: entry for entry in git.values()}
    out = []
    for row in rows:
        name = row.get("name")
        cloned = by_remote.get(_without_userinfo(row.get("source") or "")) or git.get(name)
        if name in local:
            out.append({"name": name, "origin": "world"})
        elif cloned:
            out.append({"name": name, "origin": "git", **cloned})
        else:
            out.append({"name": name, "origin": "path"})
    return out


def _without_userinfo(url: str) -> str:
    """A clone URL as anyone may see it: a token a private realm was cloned with is in the URL's user part."""
    parts = urllib.parse.urlsplit(url)
    if parts.username or parts.password:
        parts = parts._replace(netloc=parts.hostname + (f":{parts.port}" if parts.port else ""))
    return urllib.parse.urlunsplit(parts)


def _shipped_lenses(image: str, where: str) -> set[str]:
    """Lens ids that arrive as files — the world's own config/lenses, and every realm's lenses/ — and so are not the
    person's saved lenses, though the API lists them together."""
    run = _helper(image, f"cd {WORLD_BASE} && ls -1 {where}/config/lenses/*.yml {where}/config/realms/*/lenses/*.yml "
                         f".realm-cache/*/lenses/*.yml realms/*/lenses/*.yml 2>/dev/null; true")
    return {os.path.basename(p)[:-4] for p in run.stdout.decode(errors="replace").split()}


def _graph_held(api: Api, shipped: set[str]) -> tuple[list[dict], list[dict]]:
    """Saved lenses and watches, which live in the graph: their definitions from the API, delivery secrets removed."""
    lenses = []
    for item in api.call("GET", "/lenses") or []:
        if item["id"] in shipped:
            continue
        lens = api.call("GET", f"/lenses/{urllib.request.quote(item['id'])}", ok_missing=True)
        if lens and lens.get("script"):  # a lens with no script is not one the API can save
            lenses.append({k: lens.get(k) for k in ("id", "name", "script", "persona", "params")})
    watches = []
    for w in api.call("GET", "/watches") or []:
        delivery = w.get("delivery")
        if delivery:
            delivery = {k: v for k, v in delivery.items() if k not in ("secret", "headers")}
        watches.append({k: w.get(k) for k in ("name", "lensId", "params", "cron", "diffSpec", "firstRunPolicy", "enabled")}
                       | {"delivery": delivery})
    return lenses, watches


def _without_secrets(items: list[dict], kind: str, key: str, withheld: list[str]) -> list[dict]:
    """What the API returned, held to the same scan as the files: a lens script or watch with a key in it stays here."""
    kept = []
    for item in items:
        found = bundlesecrets.shapes_in(json.dumps(item).encode())
        if found:
            withheld.append(f"{kind} {item.get(key)} — holds {found[0]}; set it up again on the other appliance")
        else:
            kept.append(item)
    return kept


def _setup_manifest(where, files, themes, realms, lenses, watches, withheld) -> dict:
    items: dict[str, list[str]] = {"realms": [r["name"] for r in realms]}
    for f in files:
        items.setdefault(f["kind"], []).append(f["path"])
    items["lenses"] = [lens["id"] for lens in lenses]
    items["watches"] = [w["name"] for w in watches]
    items["themes"] = themes
    world = where.split("/", 1)[1]
    left_out = {
        "secrets": "never: credentials, keys and tokens, per-world secrets.env, and files named like them",
        "data": "everything the world learned — the graph's records, chat, memories, documents, the persona written "
                "from email and calendar, caches",
        "browser-only": "kept in the browser that set them, not on the appliance: " + ", ".join(BROWSER_ONLY),
    }
    if withheld:
        left_out["withheld (would have carried a credential)"] = "; ".join(withheld)
    return {
        "format": FORMAT,
        "kind": "setup",
        "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "appliance": {"engine": graph_engine(), "store": stores.current_store() if graph_engine() == "EMBABEL" else "neo4j"},
        "export": "live — read through the running app",
        "embedding": {"model": env_file_value("ASSISTANT_EMBEDDING_MODEL")},
        "setup": {"world": world, "worlds": {world: items}, "files": len(files)},
        "leftOut": left_out,
        "rawBytes": sum(f["bytes"] for f in files),
    }


# ── import ──────────────────────────────────────────────────────────────────────────────────────────


def import_setup(path: str, mode: str, world: str | None = None, confirm=None) -> dict:
    """The setup at [path] added to one world here; returns {installed, failed, notes}."""
    from .bundleimport import check_compatible, read_manifest
    manifest = read_manifest(path)
    check_compatible(manifest)
    if confirm and not confirm(manifest):
        raise BundleError("Nothing was changed.")
    image = mode_image(mode)
    api = Api(mode)
    api.call("GET", "/realms")          # the first authenticated call makes the user's world when it is new
    where = _world_dir(image, api.user, world or manifest["setup"]["world"].split("/", 1)[-1])
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = f"{STAGING}/setup-import-{stamp}"
    report = {"installed": [], "failed": [], "notes": []}
    _unpack(image, path, staging)
    try:
        _place_files(image, staging, where, stamp, manifest, report)
        realms = json.loads(_helper(image, f"cat {staging}/setup/realms.json").stdout or b"[]")
        lenses = json.loads(_helper(image, f"cat {staging}/setup/lenses.json").stdout or b"[]")
        watches = json.loads(_helper(image, f"cat {staging}/setup/watches.json").stdout or b"[]")
    finally:
        _helper(image, f"rm -rf {staging}")
    if items_of(manifest).get("themes"):
        _restart(mode, report)
    _install_realms(api, realms, report)
    _save_graph_held(api, lenses, watches, report)
    _verify(api, image, where, manifest, realms, lenses, watches, report)
    _notes(manifest, report)
    return report


def _unpack(image: str, path: str, staging: str) -> None:
    from .bundleimport import _unpack as unpack
    unpack(image, path, staging)


def _place_files(image: str, staging: str, where: str, stamp: str, manifest: dict, report: dict) -> None:
    """The world's files written beside what is there, each one it replaces kept first; custom themes likewise."""
    keep = f"{WORLD_BASE}/.setup-import-{stamp}"
    script = f"""
        set -e
        src={staging}/setup/world; dst={WORLD_BASE}/{where}
        mkdir -p "$dst"
        if [ -d "$src" ]; then cd "$src"; find . -type f | while IFS= read -r f; do
          if [ -e "$dst/$f" ]; then mkdir -p "{keep}/{where}/$(dirname "$f")"; cp -p "$dst/$f" "{keep}/{where}/$f"; fi
          mkdir -p "$dst/$(dirname "$f")"; cp "$f" "$dst/$f"; done; fi
        if [ -d {staging}/setup/themes ]; then mkdir -p {WORLD_BASE}/{THEMES}; cd {staging}/setup/themes; for f in *.css; do
          [ -e "{WORLD_BASE}/{THEMES}/$f" ] && {{ mkdir -p "{keep}/{THEMES}"; cp -p "{WORLD_BASE}/{THEMES}/$f" "{keep}/{THEMES}/"; }}
          cp "$f" {WORLD_BASE}/{THEMES}/; done; fi
        [ -d "{keep}" ] && find "{keep}" -type f | wc -l || echo 0
    """
    run = _helper(image, script)
    if run.returncode != 0:
        raise BundleError(f"The setup's files could not be written into {where}: {run.stderr.decode(errors='replace').strip()[-300:]}")
    files = manifest["setup"].get("files", 0)
    replaced = int((run.stdout.decode().split() or ["0"])[-1])
    report["installed"].append(f"{files} files into {where}" + (f" ({replaced} replaced; the old ones are in .setup-import-{stamp})" if replaced else ""))


def items_of(manifest: dict) -> dict:
    setup = manifest.get("setup") or {}
    return (setup.get("worlds") or {}).get(setup.get("world"), {})


def _restart(mode: str, report: dict) -> None:
    """The server reads its themes once, as it starts; a setup that brings one restarts the app, and waits for it."""
    service = MODE_SERVICE[mode]
    print("  Restarting the app, which reads custom themes as it starts.")
    _compose(mode, "restart", service, capture=True)
    started = time.monotonic()
    while time.monotonic() - started < 600:
        time.sleep(5)
        container = service_container(service)
        run = _docker("inspect", "-f", "{{.State.Health.Status}}", container, timeout=15) if container else None
        if run and run.returncode == 0 and run.stdout.strip() == "healthy":
            return
    raise BundleError(f"The app did not come back within ten minutes of the restart — see `{embabel_command('logs')}`.")


def _install_realms(api: Api, realms: list[dict], report: dict) -> None:
    """Each realm through the server's own install, so admission runs here; one already installed is left alone."""
    present = {r.get("name") for r in api.call("GET", "/realms") or []}
    for realm in realms:
        name = realm["name"]
        if realm["origin"] == "world":
            continue            # its files came with the world's; the refresh below loads it
        if name in present:
            report["installed"].append(f"realm {name}: already installed here")
            continue
        if realm["origin"] == "path":
            report["failed"].append(f"realm {name}: it was linked from a folder on the other machine; link the same "
                                    f"checkout here (`{embabel_command('realms link')}`) and install it from the Realms window")
            continue
        try:
            api.call("POST", "/realms", {"repo": realm["repo"], "ref": realm.get("ref"), "confirmed": True})
            report["installed"].append(f"realm {name} from {realm['repo']}" + (f"@{realm['ref']}" if realm.get("ref") else ""))
        except ApiRefused as e:
            report["failed"].append(f"realm {name}: {e}")
    try:
        api.call("POST", "/realms/refresh")
    except ApiRefused as e:
        report["failed"].append(f"the world could not be rebuilt with the new setup: {e}")


def _save_graph_held(api: Api, lenses: list[dict], watches: list[dict], report: dict) -> None:
    """Lenses, then the watches over them. The lens listing includes the world's and its realms' own lenses, which
    arrive with the template and the realms; one whose id is already here is one of those, and is left alone."""
    present = {lens.get("id") for lens in api.call("GET", "/lenses") or []}
    for lens in lenses:
        if lens["id"] in present:
            continue
        try:
            api.call("PUT", "/lenses", {k: lens.get(k) for k in ("name", "script", "persona", "params")})
            report["installed"].append(f"lens {lens['id']}")
        except ApiRefused as e:
            report["failed"].append(f"lens {lens['id']}: {e}")
    for watch in watches:
        try:
            api.call("POST", "/watches", {k: v for k, v in watch.items() if v is not None})
            report["installed"].append(f"watch {watch['name']}" + (" (its delivery's secret and headers were not carried; set them again)"
                                                                   if watch.get("delivery") else ""))
        except ApiRefused as e:
            report["failed"].append(f"watch {watch['name']}: {e}")


def _verify(api: Api, image: str, where: str, manifest: dict, realms, lenses, watches, report: dict) -> None:
    """What the server now says it has, against what the bundle held: a quiet failure becomes a listed one."""
    loaded = {r.get("name") for r in api.call("GET", "/realms") or []}
    missing = [r["name"] for r in realms if r["name"] not in loaded and not any(r["name"] in f for f in report["failed"])]
    report["failed"] += [f"realm {n}: not loaded after the rebuild — see `{embabel_command('logs')}`" for n in missing]
    saved = {lens["id"] for lens in api.call("GET", "/lenses") or []}
    report["failed"] += [f"lens {lens['id']}: not listed after saving" for lens in lenses
                         if lens["id"] not in saved and not any(lens["id"] in f for f in report["failed"])]
    items = items_of(manifest)
    for theme in items.get("themes", []):
        try:
            api.call("GET", f"/themes/{urllib.parse.quote(theme)}")
            report["installed"].append(f"theme {theme[:-4]}")
        except ApiRefused as e:
            report["failed"].append(f"theme {theme[:-4]}: not served after the restart ({e.status})")
    if CONSOLE in items.get("console preferences", []):
        _verify_console(api, report)
    apps = [p for p in items.get("apps", []) if p.startswith("data/apps/") and p.endswith(".html")]
    if apps:
        listed = _helper(image, f"cd {WORLD_BASE}/{where} && ls -1 " + " ".join(shlex.quote(a) for a in apps) + " 2>/dev/null")
        found = set(listed.stdout.decode().split())
        report["failed"] += [f"app {a}: not on disk after the import" for a in apps if a not in found]


def _verify_console(api: Api, report: dict) -> None:
    """The console preferences as the server now reads them: the file arrived, and the server can read it."""
    try:
        stored = api.call("GET", "/profile/console", ok_missing=True)
    except ApiRefused as e:
        report["failed"].append(f"console preferences: not readable after the import ({e.status})")
        return
    if stored is None:
        report["notes"].append("This appliance's server does not read console preferences yet; "
                               f"`{embabel_command('upgrade')}` and they apply from the file the import wrote.")
    elif stored.get("entries"):
        report["installed"].append(f"console preferences ({len(stored['entries'])} settings: theme, layout, dock and the like)")
    else:
        report["failed"].append("console preferences: the server reads none after the import")


def _notes(manifest: dict, report: dict) -> None:
    theirs = (manifest.get("embedding") or {}).get("model")
    ours = env_file_value("ASSISTANT_EMBEDDING_MODEL")
    if theirs and theirs != ours:
        report["notes"].append(f"The other appliance embeds with {theirs}, this one with {ours or 'no model'}; "
                               f"`{embabel_command('embeddings use ' + theirs)}` switches (and re-embeds).")
    if CONSOLE not in items_of(manifest).get("console preferences", []):
        report["notes"].append("The bundle holds no console preferences — it was made before the console kept them on the "
                               "appliance, or nobody had changed one — so theme, wallpaper, layout and pins are this browser's own.")
    report["notes"].append("A browser already open shows the imported theme, layout and pins when it is reloaded.")
    withheld = (manifest.get("leftOut") or {}).get("withheld (would have carried a credential)")
    if withheld:
        report["notes"].append(f"Not carried because they hold a credential: {withheld}")

