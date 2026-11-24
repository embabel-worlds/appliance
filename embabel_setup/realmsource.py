"""A realm checkout's declarations, read the way the host reads them, with no appliance running.

Two sources, one shape. A realm with a `realm.ts` is the value that file evaluates to (TYPESCRIPT_REALMS
"Source selection": a realm.ts wins and the YAML beside it is ignored); one without is its realm.yml
and the list documents under types/, producers/, views/ and apis/. Either way the result is

    {"name", "description", ..., "types": [...], "producers": [...], "views": [...], "apis": [...],
     "source": "realm.ts" | "yaml", "files": {kind: [realm-relative path, ...]}, "problems": [...]}

with every type, producer and api carrying its `name`, as the YAML lists do — realm.ts keys those
records by name instead, and the host files them as lists, so this does too.

YAML IS READ THE HOST'S WAY, not PyYAML's. PyYAML resolves YAML 1.1, where `yes` is true and
`2026-07-28` is a date; the host types only booleans, null and plain numbers. A key written twice in
one mapping, which PyYAML would quietly keep the last of, is a problem here as it is a refusal there.
The same reader is in scripts/realm-yml-to-ts.py's lineage; one definition is the point.

realm.ts IS EVALUATED BY NODE, which erases its types and runs it (Node 22.6 or later). Definition
code is deterministic and side-effect free by contract, so running it is reading it. A machine with
no Node cannot read a realm.ts, and that is said, not guessed around.

PyYAML is the one dependency outside the standard library, as for `embabel sample` from a .yml set:
a YAML parser is a solved problem, and a hand-rolled one is wrong in exactly the places nobody tested.
"""

from __future__ import annotations
import json
import os
import re
import subprocess

try:
    import yaml
except ImportError:  # pragma: no cover - the caller says what to install
    yaml = None

LIST_KINDS = ("types", "producers", "views", "apis")
# Folders a realm keeps resources in, never declarations: a compose file under stack/ is not a view.
RESOURCE_DIRS = {"tests", "test", ".github", "stack", "seed", "data", "apps", "skills", "src", "wasm",
                 "dist", "node_modules", "scripts", "docs", "lib", "generated", "dependencies", "ontology"}


class RealmSourceError(Exception):
    """The checkout cannot be read at all: no realm.yml or realm.ts, or no way to read it here."""


if yaml is not None:
    class HostYamlLoader(yaml.SafeLoader):
        """YAML typed as the host types it: true/false, null and plain numbers; the rest stay strings."""

    HostYamlLoader.yaml_implicit_resolvers = {}
    for _tag, _pattern, _first in (
            ("tag:yaml.org,2002:bool", r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF"),
            ("tag:yaml.org,2002:int", r"^[-+]?(?:0|[1-9][0-9]*)$", "-+0123456789"),
            ("tag:yaml.org,2002:float",
             r"^[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][-+]?[0-9]+)?$|^[-+]?\.(?:inf|Inf|INF)$|^\.(?:nan|NaN|NAN)$",
             "-+0123456789."),
            ("tag:yaml.org,2002:null", r"^(?:~|null|Null|NULL|)$", ["~", "n", "N", ""])):
        HostYamlLoader.add_implicit_resolver(_tag, re.compile(_pattern), list(_first))

    def _mapping(loader, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(None, None, f"the key {key!r} is written twice in one mapping",
                                                        key_node.start_mark)
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    HostYamlLoader.construct_mapping = _mapping


def read_yaml(path: str):
    if yaml is None:
        raise RealmSourceError("reading a YAML realm needs PyYAML: pip install pyyaml")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    loader = HostYamlLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def evaluate_realm_ts(path: str) -> dict:
    """The value realm.ts evaluates to. Node erases the types; the default export is the declaration."""
    script = ("import(require('url').pathToFileURL(process.argv[1]).href)"
              ".then(m => process.stdout.write(JSON.stringify(m.default)))"
              ".catch(e => { console.error(String(e)); process.exit(2) })")
    # Node 22.6 to 23.5 strip types only when asked; later Nodes do it anyway and accept the flag.
    # A Node too old to know the flag says "bad option", and is tried once more bare.
    run = None
    for flags in (["--experimental-strip-types", "--no-warnings"], []):
        try:
            run = subprocess.run(["node", *flags, "-e", script, "--", os.path.abspath(path)],
                                 capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            raise RealmSourceError("reading a realm.ts needs Node 22.6 or later, which evaluates it; none is installed")
        if "bad option" not in run.stderr:
            break
    if run.returncode != 0:
        raise RealmSourceError(f"realm.ts does not evaluate: {run.stderr.strip()[:300]}")
    value = json.loads(run.stdout or "null")
    if not isinstance(value, dict):
        raise RealmSourceError("realm.ts has no default export object")
    return value


def _named(record: dict) -> list:
    """realm.ts keys types, producers and apis by name; the host files them as named list entries."""
    out = []
    for key, spec in (record or {}).items():
        entry = dict(spec) if isinstance(spec, dict) else {"value": spec}
        entry.setdefault("name", key)
        out.append(entry)
    return out


def _yaml_files(root: str, kind: str) -> list:
    folder = os.path.join(root, kind)
    found = []
    for here, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.endswith((".yml", ".yaml")):
                found.append(os.path.join(here, f))
    return found


def read_realm(root: str) -> dict:
    root = os.path.abspath(root)
    realm_ts = os.path.join(root, "realm.ts")
    realm_yml = next((os.path.join(root, f) for f in ("realm.yml", "realm.yaml", "pack.yml")
                      if os.path.isfile(os.path.join(root, f))), None)
    if os.path.isfile(realm_ts):
        definition = evaluate_realm_ts(realm_ts)
        realm = {k: v for k, v in definition.items() if k not in LIST_KINDS}
        # Views may be a list (the YAML order) or a record keyed by name, as the other kinds are.
        for kind in LIST_KINDS:
            value = definition.get(kind) or {}
            realm[kind] = _named(value) if isinstance(value, dict) else [v for v in value if isinstance(v, dict)]
        for kind in LIST_KINDS:
            for entry in realm[kind]:
                entry["_file"] = "realm.ts"
        realm.update(source="realm.ts", files={"realm": ["realm.ts"]}, problems=[])
        return realm
    if not realm_yml:
        raise RealmSourceError(f"{root} has neither realm.ts nor realm.yml")
    problems = []
    try:
        realm = read_yaml(realm_yml) or {}
    except (yaml.YAMLError, ValueError) as e:
        raise RealmSourceError(f"{os.path.basename(realm_yml)} does not parse: {e}")
    if not isinstance(realm, dict):
        raise RealmSourceError(f"{os.path.basename(realm_yml)} is not a mapping")
    files = {"realm": [os.path.basename(realm_yml)]}
    for kind in LIST_KINDS:
        entries = []
        files[kind] = []
        for path in _yaml_files(root, kind):
            rel = os.path.relpath(path, root)
            try:
                doc = read_yaml(path)
            except (yaml.YAMLError, ValueError) as e:
                problems.append(f"{rel} does not parse: {str(e).splitlines()[0]}")
                continue
            if doc is None:
                continue
            if not isinstance(doc, list):
                # An OpenAPI document vendored under apis/ is a resource the apis.yml entry names.
                if kind != "apis":
                    problems.append(f"{rel} is not a list of {kind}")
                continue
            files[kind].append(rel)
            for entry in doc:
                if isinstance(entry, dict):
                    entry = dict(entry)
                    entry["_file"] = rel
                    entries.append(entry)
        realm[kind] = entries
    realm.update(source="yaml", files=files, problems=problems)
    return realm


def api_documents(root: str, realm: dict) -> dict:
    """{api name: parsed OpenAPI document} for every vendored spec the realm's apis entries name.

    A remote URL is not fetched: the audit reads the checkout, and a manifest it cannot see is a
    heuristic it does not run.
    """
    docs = {}
    for api in realm.get("apis") or []:
        url = api.get("url")
        if not isinstance(url, str) or re.match(r"^https?://", url, re.IGNORECASE):
            continue
        path = os.path.normpath(os.path.join(root, "apis", url))
        if not os.path.isfile(path):
            path = os.path.normpath(os.path.join(root, url))
        if not os.path.isfile(path):
            continue
        try:
            if path.endswith(".json"):
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
            else:
                doc = read_yaml(path)
        except (ValueError, OSError) + ((yaml.YAMLError,) if yaml else ()):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("paths"), dict):
            docs[api.get("name") or os.path.splitext(os.path.basename(url))[0]] = doc
    return docs
