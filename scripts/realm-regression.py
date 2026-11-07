#!/usr/bin/env python3
"""Install every realm into one world, record what loaded, run every view — as JSON a diff can read.

A regression run for the realm loader. Pointed at a running server (an in-process build on its own
port, or a fresh appliance instance), it installs a PINNED set of realm checkouts by path, records
what each one became — the name it is listed under, its version, every loading problem by exact
text, the types, producers, verbs, APIs, views, apps and keys it brought — and then invokes every
view it ships on its defaults. The output is one JSON document with stable ordering, so a run on a
baseline build and a run on a candidate build compare with `realm-regression-diff.py`. See
`.claude/skills/realm-regression/SKILL.md` for when to run it and how to read the result.

    # once: discover the realm repos, clone each at its default-branch HEAD, pin the shas
    scripts/realm-regression.py pin --realms-dir ~/scratch/regress/realms

    # per server: install the pinned set, record, sweep, write JSON + a summary table
    scripts/realm-regression.py run --base http://127.0.0.1:8045 --user <u> --password <p> \\
        --realms-dir ~/scratch/regress/realms --out base.json

Four decisions here are deliberate, because each is a way a regression run reports a difference
that is not there, or misses one that is:

  - THE SOURCES ARE PINNED. `pin` writes `realms.lock.json` beside the clones, and `run` refuses
    a checkout whose HEAD is not the pinned sha. A baseline and a candidate that installed
    different realm commits are not comparable, and the diff cannot tell you so.
  - ONE WORLD, EVERY REALM. Realms share labels and join on each other's keys, and the realistic
    shape is a world holding many of them. Installed one at a time in sorted order, then refreshed
    once and left to settle, so the order is the same on every run.
  - EXACT TEXT, NOT VERDICTS. A problem is recorded by its severity, source and message as the
    server wrote them. A regression is a new string, and a summary that says "degraded" twice
    hides two different failures.
  - VOLATILE FIELDS STAY OUT OF THE FACTS. Timings live under `timings`, which the diff ignores;
    row counts are kept, because a view that drops to zero rows is a finding.

Stdlib only: this has to run on a checkout with nothing installed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ORG = "embabel-worlds"
LOCK = "realms.lock.json"
DEFAULT_WORLD_REALMS = "repos/embabel-worlds/default-world/contents/config/realms.yml"

# A realm listed under its capture hash, or at 0.0.0, is one whose manifest the server could not
# read: the name a user sees is not the one the realm declares. Both are findings in their own
# right, and both are what the realm.ts loading work is meant to stop.
HASH_NAME = re.compile(r"^[0-9a-f]{12,}$")
FORMAT_ONLY_VERSION = "0.0.0"


# ── realm discovery and pinning ──────────────────────────────────────────────────────────────


def gh_json(*argv: str):
    out = subprocess.run(["gh", *argv], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def discover() -> list[dict]:
    """Every non-archived realm-* repo in the org, plus whatever the default world installs.

    Read at run time rather than listed here, because a list in this file is out of date the day
    somebody publishes a realm, and a regression run that silently skips the newest realm is the
    one most likely to miss the regression."""
    repos = gh_json(
        "repo", "list", ORG, "--limit", "500", "--no-archived",
        "--json", "name,url,defaultBranchRef",
    ) or []
    found = {
        r["name"]: {"repo": r["name"], "url": f"{r['url']}.git", "default": False}
        for r in repos if r["name"].startswith("realm-")
    }
    # The default world's realms.yml is YAML; the subset it uses (`repo: <url>` lines) is a line
    # format, so a line match is the honest parse here rather than a YAML dependency.
    try:
        content = gh_json("api", DEFAULT_WORLD_REALMS)
        text = base64.b64decode(content["content"]).decode()
        for url in re.findall(r"^\s*repo:\s*(\S+)\s*$", text, re.M):
            name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            found.setdefault(name, {"repo": name, "url": url, "default": False})["default"] = True
    except (subprocess.CalledProcessError, KeyError, TypeError) as e:
        print(f"WARN: could not read the default world's realm list ({e}); org repos only", file=sys.stderr)
    return [found[k] for k in sorted(found)]


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *argv], check=True, capture_output=True, text=True).stdout.strip()


def checkout_at(entry: dict, target: Path) -> None:
    """A shallow clone at exactly the pinned sha. `fetch <sha>` works on GitHub for any reachable
    commit, so a candidate run on another machine gets the same bytes as the baseline."""
    target.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").exists():
        git(target, "init", "-q")
        git(target, "remote", "add", "origin", entry["url"])
    git(target, "fetch", "-q", "--depth", "1", "origin", entry["sha"])
    git(target, "checkout", "-q", "--detach", "FETCH_HEAD")


def pin(args) -> int:
    realms_dir = Path(args.realms_dir).expanduser().resolve()
    realms_dir.mkdir(parents=True, exist_ok=True)
    lock_path = realms_dir / LOCK
    entries = discover()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["repo"] in wanted]
    for e in entries:
        target = realms_dir / e["repo"]
        if not target.exists():
            subprocess.run(["git", "clone", "-q", "--depth", "1", e["url"], str(target)], check=True)
        elif args.refresh:
            git(target, "fetch", "-q", "--depth", "1", "origin", "HEAD")
            git(target, "checkout", "-q", "--detach", "FETCH_HEAD")
        e["sha"] = git(target, "rev-parse", "HEAD")
        print(f"{e['repo']:40} {e['sha'][:12]}{'  (default world)' if e['default'] else ''}")
    lock = {"org": ORG, "pinnedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "realms": entries}
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(f"{len(entries)} realms pinned in {lock_path}")
    return 0


def pinned(realms_dir: Path, only: str | None, add_paths: list[str]) -> list[dict]:
    """The realms this run installs: the lock's, restored to their pinned shas, plus any extra
    local paths. A checkout that has moved is restored rather than trusted — the point of the lock
    is that nobody has to remember whether they pulled."""
    lock_path = realms_dir / LOCK
    if not lock_path.exists():
        sys.exit(f"No {lock_path}. Run `{sys.argv[0]} pin --realms-dir {realms_dir}` first.")
    entries = json.loads(lock_path.read_text())["realms"]
    if only:
        wanted = set(only.split(","))
        entries = [e for e in entries if e["repo"] in wanted]
    for e in entries:
        target = realms_dir / e["repo"]
        head = git(target, "rev-parse", "HEAD") if (target / ".git").exists() else None
        if head != e["sha"]:
            print(f"restoring {e['repo']} to its pinned {e['sha'][:12]} (was {head and head[:12]})", file=sys.stderr)
            checkout_at(e, target)
        e["path"] = str(target)
    for p in add_paths:
        path = Path(p).expanduser().resolve()
        sha = git(path, "rev-parse", "HEAD") if (path / ".git").exists() else None
        entries.append({"repo": path.name, "url": None, "default": False, "sha": sha, "path": str(path), "local": True})
    return sorted(entries, key=lambda e: e["repo"])


def realm_format(path: Path) -> str:
    """Which definition the server will read. Recorded so a twin comparison (`--parity`) can drop
    it, and so a ts-only realm's failures can be told from a YAML realm's at a glance."""
    has_ts, has_yml, has_pack = (path / "realm.ts").exists(), (path / "realm.yml").exists(), (path / "pack.yml").exists()
    return "+".join(x for x, present in (("ts", has_ts), ("yml", has_yml), ("pack", has_pack)) if present) or "none"


# ── the server ───────────────────────────────────────────────────────────────────────────────


class Client:
    def __init__(self, base: str, user: str | None, password: str | None, token: str | None):
        self.base = base.rstrip("/")
        if token:
            self.auth = f"Bearer {token}"
        else:
            self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(self, method: str, path: str, body=None, timeout: float = 120.0) -> tuple[int, object]:
        """(status, parsed body). Status 0 is a transport failure and -1 a timeout, both carried
        as outcomes: a realm that hangs the server is a result, not a reason to stop the run."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": self.auth, "Accept": "application/json",
                     **({"Content-Type": "application/json"} if data is not None else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, parse(r.read())
        except urllib.error.HTTPError as e:
            return e.code, parse(e.read())
        except TimeoutError:
            return -1, "TIMEOUT"
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                return -1, "TIMEOUT"
            return 0, str(e.reason)
        except Exception as e:  # noqa: BLE001 — a transport failure is an outcome, not a crash
            return 0, f"{type(e).__name__}: {e}"


def parse(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")[:2000]


def instance_base(instance: str) -> str:
    """An appliance instance's API origin: its port block's base + 1, as the install prints."""
    env = REPO / (".env" if instance == "appliance" else f".env.{instance}")
    if env.exists():
        m = re.search(r"^\s*EMBABEL_PORT_BASE\s*=\s*(\d+)", env.read_text(), re.M)
        if m:
            return f"http://127.0.0.1:{int(m.group(1)) + 1}"
    sys.exit(f"No EMBABEL_PORT_BASE in {env}. Pass --base, or name an installed instance.")


def wait_ready(client: Client, seconds: int) -> None:
    deadline = time.time() + seconds
    while True:
        status, body = client.call("GET", "/api/v1/realms", timeout=30)
        if status == 200:
            return
        if time.time() > deadline:
            sys.exit(f"server at {client.base} not ready after {seconds}s: {status} {str(body)[:200]}")
        time.sleep(5)


def listing(client: Client) -> dict[str, dict]:
    status, body = client.call("GET", "/api/v1/realms", timeout=300)
    if status != 200 or not isinstance(body, list):
        sys.exit(f"realm listing failed: {status} {str(body)[:300]}")
    return {r.get("name"): r for r in body}


def settle(client: Client, seconds: int) -> dict[str, dict]:
    """The listing once two reads 10s apart agree on every realm's name, version and problem
    count. Installs kick off background work (reference seeds, ingests, Shinar captures), and a
    snapshot taken mid-flight makes two identical runs disagree."""
    deadline, previous = time.time() + seconds, None
    while True:
        rows = listing(client)
        shape = sorted((n or "", str(r.get("version")), str(r.get("problems"))) for n, r in rows.items())
        if shape == previous or time.time() > deadline:
            return rows
        previous = shape
        time.sleep(10)


# ── one run ──────────────────────────────────────────────────────────────────────────────────


def problem_key(p: dict) -> dict:
    return {k: p.get(k) for k in ("severity", "source", "message", "file", "path", "line")}


def same_realm(row: dict, repo: str) -> bool:
    """Whether a listed realm is this repo, installed some other way — by git, from a world
    template's manifest. Matched on the clone URL when there is one, else on the name."""
    source = str(row.get("source") or "").rstrip("/").removesuffix(".git")
    name = row.get("name") or ""
    return source.rsplit("/", 1)[-1] == repo or name in (repo, repo.removeprefix("realm-"))


def install_all(client: Client, realms: list[dict], mount_prefix: str | None, log) -> dict:
    """Every realm by path, in sorted order. A realm the world already holds — the default world
    installs several from git at whatever their branch says TODAY — is removed first, so what is
    tested is the pinned checkout and not a moving one."""
    before = listing(client)
    removed = []
    for name, row in sorted(before.items()):
        if any(same_realm(row, e["repo"]) for e in realms):
            status, body = client.call("DELETE", f"/api/v1/realms/{urllib.parse.quote(name, safe='')}", timeout=600)
            removed.append({"name": name, "http": status})
            log(f"removed preinstalled {name}: {status}")
    results: dict[str, dict] = {}
    # Realms build on each other's types (a scripture realm's parents live in a vocabulary realm
    # that sorts after it), and an install whose references do not resolve is rolled back. So the
    # refused are offered again, in the same order, until a pass installs nothing new — the
    # result an operator gets by installing in dependency order, reached without encoding one.
    pending, attempt = list(realms), 0
    while pending:
        attempt += 1
        refused = []
        for e in pending:
            path = f"{mount_prefix.rstrip('/')}/{e['repo']}" if mount_prefix else e["path"]
            status, body = client.call("POST", "/api/v1/realms", {"path": path}, timeout=900)
            body = body if isinstance(body, dict) else {"raw": body}
            outcome = {
                "http": status,
                "status": body.get("status"),
                "code": body.get("code"),
                "message": body.get("message") or body.get("error") or body.get("raw"),
                "declaredName": body.get("name"),
                "admission": body.get("admission"),
                "attempt": attempt,
            }
            if e["repo"] in results:
                outcome["firstRefusal"] = results[e["repo"]].get("firstRefusal") or {
                    k: results[e["repo"]][k] for k in ("http", "code", "message")}
            results[e["repo"]] = outcome
            log(f"install {e['repo']} (pass {attempt}): {status} {body.get('status') or body.get('code') or ''} -> {body.get('name') or ''}")
            if status != 201:
                refused.append(e)
        if len(refused) == len(pending):
            break
        pending = refused
    return {"preinstalled": sorted(n for n in before if n), "removedFirst": removed, "results": results}


def realm_status(client: Client, name: str) -> dict:
    """The realm_status tool through the REST tool gateway: the one read that says what a loaded
    realm actually granted — verbs, APIs, producers, types, schedules — and its problems."""
    status, body = client.call("POST", "/api/v1/tools/realm_status", {"realm": name}, timeout=300)
    if isinstance(body, dict) and "result" in body:
        body = body["result"]
        if isinstance(body, str):
            body = parse(body.encode())
    if isinstance(body, str):
        body = parse(body.encode())
    return {"http": status, "body": body}


def apps_served(client: Client, declared: str, path: Path) -> dict[str, int]:
    """Each page the realm ships under apps/, and the status the server answers it with. The
    files are the realm's claim; the status is whether the loaded realm honours it."""
    pages = sorted(p.name for p in (path / "apps").glob("*.html")) if (path / "apps").is_dir() else []
    out = {}
    for page in pages:
        status, _ = client.call("GET", f"/apps/{urllib.parse.quote(declared, safe='')}/{urllib.parse.quote(page)}", timeout=60)
        out[page] = status
    return out


def view_rows(answer: dict) -> int | None:
    data = answer.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return len(data["rows"])
    return None if data is not None else 0


def invoke(client: Client, view: dict, timeout: int) -> dict:
    """One view on its defaults, classified. A failure is invoked once more, to tell a broken view
    from one that fails only on first touch — both real, but different bugs."""
    name = view["name"]
    needs = sorted(p for p, spec in (view.get("params") or {}).items()
                   if isinstance(spec, dict) and spec.get("default") is None and spec.get("required") is not False)
    if needs:
        return {"outcome": "SKIPPED_NEEDS_PARAMS", "params": needs}
    if not view.get("cypher") and view.get("goal"):
        return {"outcome": "SKIPPED_UNPLANNED_GOAL"}

    def once():
        started = time.time()
        status, answer = client.call("POST", f"/api/v1/views/{urllib.parse.quote(name, safe='')}/invoke",
                                     {"args": {}}, timeout=timeout)
        return status, answer, time.time() - started

    status, answer, took = once()
    result = classify(status, answer)
    if result["outcome"] in ("FAILED", "ERROR"):
        status2, answer2, _ = once()
        if classify(status2, answer2)["outcome"] in ("OK", "EMPTY"):
            result["outcome"] = "FAILS_FIRST_TOUCH"
        else:
            result["detail"] = engine_reason(client, name, timeout)
    result["_ms"] = round(took * 1000)
    return result


def engine_reason(client: Client, name: str, timeout: int) -> str | None:
    """Why a view failed, in the engine's words. The public invoke envelope says only that the
    operation could not be completed — rightly, it is a consumer's — so the cause is read from
    the admin run route instead. Kept to its first two lines: the rest is schema suggestions that
    move whenever any realm's labels do, which would make every failure look new in a diff."""
    status, answer = client.call("POST", f"/api/v1/admin/kg/views/{urllib.parse.quote(name, safe='')}/run",
                                 {}, timeout=timeout)
    if status == -1:
        return "TIMEOUT"
    error = answer.get("error") if isinstance(answer, dict) else answer
    if not error:
        return None
    return "\n".join(str(error).strip().splitlines()[:2])[:500]


def classify(status: int, answer) -> dict:
    if status == -1:
        return {"outcome": "TIMEOUT"}
    if status != 200 or not isinstance(answer, dict):
        return {"outcome": "ERROR", "http": status,
                "error": answer.get("error") if isinstance(answer, dict) else str(answer)[:300]}
    error = answer.get("error") or {}
    state = answer.get("status")
    if state == "SUCCEEDED":
        rows = view_rows(answer)
        return {"outcome": "OK" if rows else "EMPTY", "rows": rows, "status": state,
                "resultOutcome": answer.get("outcome")}
    return {"outcome": "FAILED", "status": state, "resultOutcome": answer.get("outcome"),
            "errorCode": error.get("code"), "reason": error.get("reason"), "message": error.get("message")}


def names_of(items) -> list[str]:
    out = []
    for it in items or []:
        out.append(it if isinstance(it, str) else str(it.get("name") or it.get("id") or json.dumps(it, sort_keys=True)))
    return sorted(out)


def run(args) -> int:
    realms_dir = Path(args.realms_dir).expanduser().resolve()
    realms = pinned(realms_dir, args.only, args.add_path or [])
    base = args.base or instance_base(args.instance)
    password = args.password or os.environ.get("REALM_REGRESSION_PASSWORD")
    client = Client(base, args.user, password, args.token or os.environ.get("REALM_REGRESSION_TOKEN"))
    log = (lambda m: print(m, file=sys.stderr, flush=True))
    started = time.time()
    wait_ready(client, args.ready_timeout)

    installs = install_all(client, realms, args.mount_prefix, log)
    log("refreshing the world once, then waiting for the listing to settle")
    refresh_status, refresh_body = client.call("POST", "/api/v1/realms/refresh", {}, timeout=900)
    listed = settle(client, args.settle_timeout)

    status, views = client.call("GET", "/api/v1/admin/kg/views", timeout=300)
    views = views if isinstance(views, list) else []
    _, keys = client.call("GET", "/api/v1/keys", timeout=120)
    _, types = client.call("GET", "/api/v1/world/types", timeout=120)
    _, actions = client.call("GET", "/api/v1/world/actions", timeout=120)
    _, handlers = client.call("GET", "/api/v1/handlers", timeout=120)
    keys = keys if isinstance(keys, list) else []
    handlers = handlers if isinstance(handlers, list) else []

    out_realms: dict[str, dict] = {}
    timings: dict[str, int] = {}
    for e in realms:
        inst = installs["results"][e["repo"]]
        declared = inst["declaredName"] or e["repo"]
        row = listed.get(declared)
        st = realm_status(client, declared)
        report = st["body"] if isinstance(st["body"], dict) else {}
        surface = report.get("surface") or {}
        p_status, problems = client.call("GET", f"/api/v1/realms/{urllib.parse.quote(declared, safe='')}/problems", timeout=120)
        problems = problems if isinstance(problems, list) else []
        status_problems = [problem_key({**p, **(p.get("location") or {})}) for p in report.get("problems") or []
                           if isinstance(p, dict)]
        realm_views = sorted((v for v in views if v.get("source") == declared), key=lambda v: v["name"])
        realm_keys = sorted(k.get("name") for k in keys if declared in (k.get("realms") or []))
        realm_handlers = sorted(h.get("name") for h in handlers if h.get("realm") == declared)
        listed_name = row.get("name") if row else None
        out_realms[e["repo"]] = {
            "repo": e["repo"],
            "twinOf": e["repo"].removesuffix(args.twin_suffix) if args.twin_suffix and e["repo"].endswith(args.twin_suffix) else None,
            "sha": e.get("sha"),
            "format": realm_format(Path(e["path"])),
            "defaultWorld": e.get("default", False),
            "install": inst,
            "listed": {
                "name": listed_name,
                "version": row.get("version") if row else None,
                "problems": row.get("problems") if row else None,
                "missingSources": row.get("missingSources") if row else None,
                "underHash": bool(listed_name and HASH_NAME.match(listed_name)),
                "zeroVersion": bool(row and row.get("version") == FORMAT_ONLY_VERSION),
                "absent": row is None,
            },
            "status": {
                "http": st["http"],
                "installed": report.get("installed"),
                "state": report.get("status"),
                "admission": report.get("admission"),
                "grantedCapabilities": sorted(report.get("grantedCapabilities") or []),
                "error": None if report else str(st["body"])[:500],
            },
            "surface": {
                "types": sorted(surface.get("types") or []),
                "producers": sorted(surface.get("producers") or []),
                "verbs": sorted(surface.get("verbs") or []),
                "apis": sorted(surface.get("apis") or []),
                "schedules": sorted(surface.get("schedules") or []),
                "inertApis": names_of(surface.get("inertApis")),
                "missingSources": names_of(surface.get("missingSources")),
            },
            "problems": sorted((problem_key(p) for p in problems), key=lambda p: json.dumps(p, sort_keys=True)),
            "problemsHttp": p_status,
            "statusProblems": sorted(status_problems, key=lambda p: json.dumps(p, sort_keys=True)),
            "views": {v["name"]: {"materialized": v.get("materialized"),
                                  "params": sorted((v.get("params") or {}).keys())} for v in realm_views},
            "handlers": realm_handlers,
            "apps": apps_served(client, declared, Path(e["path"])),
            "keys": realm_keys,
        }

    # The sweep comes after every realm is in and settled, so a view that joins across realms
    # sees the same world on every run.
    by_source = {(v.get("source"), v["name"]): v for v in views}
    unique = [(repo, name, by_source[(r["install"]["declaredName"] or repo, name)])
              for repo, r in out_realms.items() for name in r["views"]]
    log(f"sweeping {len(unique)} views, {args.jobs} at a time, {args.view_timeout}s each at most")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        swept = list(pool.map(lambda t: invoke(client, t[2], args.view_timeout), unique))
    for (repo, name, _), result in zip(unique, swept):
        timings[f"{repo}/{name}"] = result.pop("_ms", None)
        out_realms[repo]["views"][name].update(result)
        log(f"view {repo}/{name}: {result['outcome']}")

    doc = {
        "harness": "realm-regression/1",
        "server": base,
        "label": args.label,
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "serverSha": args.server_sha,
        "realmCount": len(out_realms),
        "preinstalled": installs["preinstalled"],
        "refresh": {"http": refresh_status, "realms": sorted(refresh_body.get("realms") or []) if isinstance(refresh_body, dict) else None},
        "world": {"typeCount": len(types) if isinstance(types, list) else None,
                  "actionCount": len(actions) if isinstance(actions, list) else None,
                  "viewCount": len(views)},
        "realms": {k: out_realms[k] for k in sorted(out_realms)},
        "timings": {"totalSeconds": round(time.time() - started), "viewsMs": dict(sorted(timings.items()))},
    }
    Path(args.out).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    summary = summarize(doc)
    Path(args.out).with_suffix(".txt").write_text(summary)
    print(summary)
    return 0


OUTCOMES = ("OK", "EMPTY", "FAILED", "FAILS_FIRST_TOUCH", "ERROR", "TIMEOUT", "SKIPPED_NEEDS_PARAMS", "SKIPPED_UNPLANNED_GOAL")


def summarize(doc: dict) -> str:
    lines = [f"{'realm':34} {'fmt':8} {'listed':10} {'E':>3} {'W':>3} {'N':>3} {'types':>5}  views ok/empty/fail/timeout/skip"]
    totals = dict.fromkeys(OUTCOMES, 0)
    for repo, r in doc["realms"].items():
        sev = [p["severity"] for p in r["problems"]]
        lst = r["listed"]
        listed = "ABSENT" if lst["absent"] else "HASH" if lst["underHash"] else "0.0.0" if lst["zeroVersion"] else "ok"
        outs = [v.get("outcome") for v in r["views"].values()]
        for o in outs:
            totals[o] = totals.get(o, 0) + 1
        failed = sum(o in ("FAILED", "ERROR", "FAILS_FIRST_TOUCH") for o in outs)
        skipped = sum(str(o).startswith("SKIPPED") for o in outs)
        lines.append(
            f"{repo:34} {r['format']:8} {listed:10} {sev.count('ERROR'):>3} {sev.count('WARNING'):>3} {sev.count('NOTE'):>3} "
            f"{len(r['surface']['types']):>5}  {outs.count('OK')}/{outs.count('EMPTY')}/{failed}/{outs.count('TIMEOUT')}/{skipped}"
        )
    lines.append("")
    lines.append("views: " + ", ".join(f"{k} {v}" for k, v in totals.items()))
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pin", help="discover realm repos, clone them, and pin their shas")
    p.add_argument("--realms-dir", required=True)
    p.add_argument("--only", help="comma-separated repo names to pin (default: all discovered)")
    p.add_argument("--refresh", action="store_true", help="move existing clones to their default-branch HEAD")
    p.set_defaults(func=pin)

    r = sub.add_parser("run", help="install the pinned realms into a running server, record, sweep views")
    r.add_argument("--realms-dir", required=True)
    r.add_argument("--out", required=True, help="JSON output; a summary table is written beside it as .txt")
    r.add_argument("--base", help="API origin, e.g. http://127.0.0.1:8045")
    r.add_argument("--instance", default="appliance", help="appliance instance, when --base is not given")
    r.add_argument("--user", default="demo")
    r.add_argument("--password", help="or REALM_REGRESSION_PASSWORD")
    r.add_argument("--token", help="bearer token instead of Basic, or REALM_REGRESSION_TOKEN")
    r.add_argument("--only", help="comma-separated repo names (default: every pinned realm)")
    r.add_argument("--add-path", action="append", help="also install this local realm directory (repeatable)")
    r.add_argument("--mount-prefix", help="install as <prefix>/<repo>: the realms dir as the server sees it, e.g. /realms in a container")
    r.add_argument("--view-timeout", type=int, default=60)
    r.add_argument("--jobs", type=int, default=4, help="views invoked at once (default 4)")
    r.add_argument("--ready-timeout", type=int, default=600)
    r.add_argument("--settle-timeout", type=int, default=600)
    r.add_argument("--label", default="", help="free text recorded in the output, e.g. base or candidate")
    r.add_argument("--server-sha", default=None, help="the server commit under test, recorded in the output")
    r.add_argument("--twin-suffix", default=None,
                   help="parity runs: repos ending in this suffix are twins of the repo without it")
    r.set_defaults(func=run)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
