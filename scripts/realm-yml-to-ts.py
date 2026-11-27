#!/usr/bin/env python3
"""Turn a YAML realm into a realm whose declarations live in realm.ts.

    realm-yml-to-ts.py <yaml-realm-dir> <out-dir>
    realm-yml-to-ts.py --all <realms-dir> <out-dir>
    realm-yml-to-ts.py --verify <converted-realm-dir> <original-realm-dir>

The first form copies the realm to <out-dir>, writes <out-dir>/realm.ts, and deletes from the copy
every YAML declaration file it carried into realm.ts. `--all` does that for each realm directory
under <realms-dir>, each into <out-dir>/<same directory name>; the realm keeps its name, so a world
holding the twin can be compared with a world holding the original fact for fact. `--verify`
evaluates a converted realm.ts with Node and checks what it maps to against the YAML it came from.

Requires Python 3.9+ and PyYAML (`pip install pyyaml`). `--verify`, and converting a realm whose
wasm handlers have no committed dist/manifest.json, also need Node 22.6 or later.

WHAT IT PRINTS IS THE POINT. Every declaration it could not carry stays a file in the copy and gets
one line:

    NOT CONVERTED <realm-relative path>: <reason>

That list is the gap between what YAML can declare and what realm.ts can, today. The converter never
drops a declaration quietly, and never writes one into realm.ts unless mapping it back gives the
file it came from: each file is converted, mapped back the way the host maps a realm.ts, and
compared with the file before the file is deleted. A file that does not survive the round trip is
kept and reported, with the first difference.

What it carries, and how the host files it again:

  realm.yml (or pack.yml)       the realm's top-level metadata fields
  types/, producers/, apis/,    records keyed by name; the host files each kind as one list
  webhooks/                     document, so a name declared twice cannot be carried
  views/                        a list, in file order
  actions/, goals/, lenses/,    records keyed by file name, one document each
  focuses/, events/, channels/
  dist/manifest.json            `handlers` (and `entry`, `generatedAt`), for the wasm host
  wasm/handlers.ts with no      `handlers` named the way the host names them when no manifest is
  manifest                      committed; needs --bundle-handlers, see below

Everything else stays a file: handler source, wasm/, src/, skills/, apps/, sql/*.sql, seed data,
vendored API documents, icons, tests. realm.ts names them where the spec says it does (`entry`,
`icon`, an api's `url`), and the relative paths a carried declaration holds resolve as before,
because the host files each document in the folder it came from.

For an author, the output is a starting point rather than a finished realm.ts: it is one literal
object, comments carried over from the head of each file and each list entry (comments inside an
entry are lost), with no `satisfies Realm` because the SDK's types are narrower than the host and
would refuse kinds the host accepts. Split it into modules and add the type check as the SDK grows.

A wasm realm that commits no dist/manifest.json gets its handlers from the names its entry exports.
The host reads those names without running the code, with its own bundling script; pass that script
as `--bundle-handlers <path to bundle-handlers.mjs>` so the converter names them exactly as the host
does. Without it, such a realm's handlers are reported, not guessed.
"""

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import yaml
except ImportError:  # pragma: no cover - the docstring names the dependency
    sys.exit("realm-yml-to-ts needs PyYAML: pip install pyyaml")


# YAML read the way the host reads it ------------------------------------------------------------

class HostYamlLoader(yaml.SafeLoader):
    """
    PyYAML resolves YAML 1.1: `yes` is true, `2026-07-28` is a date and `1:30` is ninety. The host's
    YAML reader types only true/false, null, and plain numbers, and keeps the rest as strings, so a
    converter reading with PyYAML's defaults would write different values than the host reads.
    """


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
    """The host refuses a mapping that writes a key twice; PyYAML would keep the last quietly."""
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"the key {key!r} is written twice in one mapping",
                                                    key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


HostYamlLoader.construct_mapping = _mapping


def read_yaml(path, aliases=False):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    loader = HostYamlLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        if not aliases and _has_alias(node):
            raise ValueError("uses a YAML alias, which the host refuses")
        return loader.construct_document(node)
    finally:
        loader.dispose()


def _has_alias(node, seen=None):
    seen = seen if seen is not None else set()
    if id(node) in seen:
        return True
    seen.add(id(node))
    if isinstance(node, yaml.SequenceNode):
        return any(_has_alias(child, seen) for child in node.value)
    if isinstance(node, yaml.MappingNode):
        return any(_has_alias(k, seen) or _has_alias(v, seen) for k, v in node.value)
    return False


# What a realm's files are -----------------------------------------------------------------------

LIST_KINDS = ("types", "producers", "apis", "webhooks", "views")
FILE_KINDS = ("actions", "goals", "lenses", "focuses", "events", "channels")
LIST_KEYED = {"types": "name", "producers": "name", "apis": "name", "webhooks": "name"}

# The identity the host reads a one-per-file document by, and the defaults it fills in.
FILE_IDENTITY = {
    "actions": ("name", {"stepType": "action"}),
    "goals": ("name", {"stepType": "goal"}),
    "lenses": ("id", {}),
    "focuses": ("name", {}),
    "channels": ("name", {}),
    "events": (None, {}),
}

# Declarations that have a YAML form and, on the host this targets, no realm.ts field.
NO_FIELD = {
    "sources.yml": "collection behaviour (sources.yml) has no realm.ts field",
    "keys.yml": "key entries (keys.yml) have no realm.ts field",
    "credentials.yml": "credentials are a captured-profile field; a conventional realm.ts has none",
    "rules": "DERIVE rule sets (rules/) have no realm.ts field",
    "reference": "reference data (reference/) has no realm.ts field",
    "agents": "agents and routines (agents/) have no realm.ts field",
    "hints": "hints (hints/) have no realm.ts field",
    "personalities": "personalities (personalities/) have no realm.ts field",
    "prompts": "prompts (prompts/) have no realm.ts field",
    "notebook": "notebook sets (notebook/) have no realm.ts field",
    "mcp": "MCP servers (mcp/) have no realm.ts field",
    "commands": "commands (commands/) have no realm.ts field",
    "decorations": "decorations (decorations/) have no realm.ts field",
    "tours": "tours (tours/) have no realm.ts field",
    "sql/datasources.yml": "SQL datasources (sql/datasources.yml) have no realm.ts field",
    "sql/procedures.yml": "SQL procedures (sql/procedures.yml) have no realm.ts field",
}

# Folders that hold resources, not declarations: copied, never read.
RESOURCE_FILES = {"docker-compose.yml", "docker-compose.yaml", "pnpm-lock.yaml", ".yamllint", ".yamllint.yml"}
RESOURCES = {"tests", "test", ".github", "stack", "seed", "data", "omnigraph", "apps", "skills", "src",
             "wasm", "dist", "node_modules", "scripts", "docs", "lib", "generated", "dependencies"}

METADATA = ("name", "version", "description", "author", "url", "icon", "category", "maturity",
            "tags", "exports", "capabilities", "realmLabel", "optionalLabels", "retry", "work",
            "host", "execution")
METADATA_LISTS = ("capabilities", "tags", "exports", "optionalLabels")

REMOTE_URL = re.compile(r"^https?://", re.IGNORECASE)


class Refused(Exception):
    """A declaration that cannot be carried, with the reason the report prints."""


# The host's mapping, realm.ts field -> documents ------------------------------------------------
#
# These mirror how the host files a conventional realm.ts's fields as the documents a YAML realm
# would hold. The converter writes the inverse and checks every file against these before
# deleting it; --verify checks the whole evaluated realm.ts against them again.

def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def without_nulls(value):
    """The host reads an absent field and a null one alike, and realm.ts cannot always say which."""
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [without_nulls(item) for item in value]
    return value


def renamed(obj, renames):
    return {renames.get(key, key): value for key, value in obj.items()}


def wire_url(url):
    if REMOTE_URL.search(url):
        return url
    kept = []
    for part in url.split("/"):
        if part in ("", "."):
            continue
        if part == ".." and kept and kept[-1] != "..":
            kept.pop()
        else:
            kept.append(part)
    if kept and kept[0] == "apis":
        kept.pop(0)
    else:
        kept.insert(0, "..")
    return "/".join(kept)


OAUTH_TO_YAML = {"clientId": "client-id", "authUrl": "auth-url", "tokenUrl": "token-url",
                 "scopes": "scopes", "provider": "provider", "pkce": "pkce", "installUrl": "install-url"}
IDENTITY_TO_YAML = {"url": "url", "accountIdField": "account-id-field", "method": "method", "auth": "auth",
                    "displayNameField": "display-name-field"}
API_TO_YAML = {"type": "type", "auth": "auth", "headers": "headers", "tags": "tags", "mutations": "mutations",
               "tokenEnv": "token-env", "operationIds": "operation-ids"}
SIGNATURE_TO_YAML = {"secretRef": "secret-ref"}
CHANNEL_TO_YAML = {"tokenEnv": "token-env", "signalType": "signal-type", "autoStart": "auto-start"}
EVENT_WEBHOOK_TO_YAML = {"signatureSecret": "signature-secret", "tierWhen": "tier-when"}


def map_api(key, spec):
    entry = {"url": wire_url(str(spec["url"])) if spec.get("url") is not None else None}
    if spec.get("type") is not None:
        entry["type"] = spec["type"]
    if "name" not in spec or spec["name"] is not None:
        entry["name"] = spec["name"] if isinstance(spec.get("name"), str) else key
    for field, as_ in API_TO_YAML.items():
        if field != "type" and spec.get(field) is not None:
            entry[as_] = spec[field]
    oauth = spec.get("oauth2")
    if isinstance(oauth, dict):
        mapped = {as_: oauth[field] for field, as_ in OAUTH_TO_YAML.items()
                  if field not in ("pkce", "installUrl") and oauth.get(field) is not None}
        if isinstance(oauth.get("identity"), dict):
            mapped["identity"] = {as_: oauth["identity"][field] for field, as_ in IDENTITY_TO_YAML.items()
                                  if oauth["identity"].get(field) is not None}
        if isinstance(oauth.get("webhookTenancy"), dict):
            tenancy = oauth["webhookTenancy"]
            mapped["webhook-tenancy"] = {"store": tenancy["store"]} if tenancy.get("store") is not None else {}
        for field in ("pkce", "installUrl"):
            if oauth.get(field) is not None:
                mapped[OAUTH_TO_YAML[field]] = oauth[field]
        entry["oauth2"] = mapped
    return canonical(entry)


def map_named(key, spec):
    if spec.get("name") is not None and spec["name"] != key:
        raise Refused(f"the entry {key!r} declares a different name than its key")
    out = dict(spec)
    out["name"] = key
    return canonical(out)


def map_type(key, spec):
    copy = dict(spec)
    if isinstance(spec.get("methods"), dict):
        copy["methods"] = [map_named(name, spec["methods"][name]) for name in sorted(spec["methods"])]
    return map_named(key, copy)


def map_signature(signature):
    out = {}
    for field, value in signature.items():
        if field == "secretRef" and isinstance(value, dict):
            out["secret-ref"] = renamed(value, {"oauthApp": "oauth-app"})
        elif field == "timestamp" and isinstance(value, dict):
            out["timestamp"] = renamed(value, {"toleranceSeconds": "tolerance-seconds"})
        else:
            out[field] = value
    return out


def map_webhook(key, spec):
    out = {field: (map_signature(value) if field == "signature" and isinstance(value, dict) else value)
           for field, value in spec.items()}
    return map_named(key, out)


def map_event(event):
    out = {}
    if event.get("type") is not None:
        out["type"] = event["type"]
    if "poll" in event:
        out["poll"] = event["poll"]
    elif isinstance(event.get("webhook"), dict):
        out["webhook"] = renamed(event["webhook"], EVENT_WEBHOOK_TO_YAML)
    return canonical(out)


def map_file_kind(kind, key, spec):
    if kind in ("actions", "goals"):
        out = {"name": key, "stepType": FILE_IDENTITY[kind][1]["stepType"]}
        out.update(spec)
    elif kind == "lenses":
        out = {"id": key}
        out.update(spec)
    elif kind == "focuses":
        out = {"name": key}
        out.update(spec)
    elif kind == "channels":
        out = renamed(spec, CHANNEL_TO_YAML)
        out["name"] = key
    elif kind == "events":
        return [map_event(event) for event in spec]
    else:
        raise AssertionError(kind)
    return canonical(out)


def map_list_entry(kind, key, spec):
    return {"types": map_type, "producers": map_named, "apis": map_api, "webhooks": map_webhook}[kind](key, spec)


def map_handler(key, spec):
    entry = {}
    if spec.get("namespace") is not None:
        entry["namespace"] = spec["namespace"]
    entry["name"] = key
    if spec.get("description") is not None:
        entry["description"] = spec["description"]
    entry["inputSchema"] = spec["input"] if spec.get("input") is not None else {"type": "object"}
    entry["outputSchema"] = spec["output"] if spec.get("output") is not None else {"type": "object"}
    for field in ("onType", "className", "schedule"):
        if spec.get(field) is not None:
            entry[field] = spec[field]
    return entry


def map_metadata(definition):
    out = {}
    for key in METADATA:
        value = definition.get(key)
        if value is None:
            continue
        if key in METADATA_LISTS and value == []:
            continue
        out[key] = value
    return out


def map_definition(definition):
    """Every document a conventional realm.ts files, as {path: tree}; list kinds by realm name."""
    name = definition.get("name")
    docs = {"realm.yml": map_metadata(definition)}
    for kind in ("types", "producers", "apis", "webhooks"):
        field = definition.get(kind) or {}
        if field:
            path = "apis/apis.yml" if kind == "apis" else f"{kind}/{name}.yml"
            docs[path] = [map_list_entry(kind, key, field[key]) for key in sorted(field)]
    if definition.get("views"):
        docs[f"views/{name}.yml"] = [canonical(view) for view in definition["views"]]
    for kind in FILE_KINDS:
        field = definition.get(kind) or {}
        for key in sorted(field):
            docs[f"{kind}/{key}.yml"] = map_file_kind(kind, key, field[key])
    handlers = definition.get("handlers") or {}
    if handlers:
        docs["dist/manifest.json"] = {
            "version": 1,
            "generatedAt": definition.get("generatedAt", "1970-01-01T00:00:00Z"),
            "entries": [map_handler(key, handlers[key]) for key in sorted(handlers)],
            **({"entry": definition["entry"]} if definition.get("entry") is not None else {}),
        }
    return docs


# The inverse: YAML documents -> realm.ts fields -------------------------------------------------

def camel(obj, to_yaml):
    """The realm.ts spelling of a YAML object whose keys the host renames on the way back."""
    to_ts = {yaml_key: ts_key for ts_key, yaml_key in to_yaml.items()}
    for key in obj:
        if key in to_yaml and to_yaml[key] != key:
            raise Refused(f"the key {key!r} is spelled the realm.ts way in YAML, and the host would rename it")
    return {to_ts.get(key, key): value for key, value in obj.items()}


def api_key(entry):
    if isinstance(entry.get("name"), str):
        return entry["name"]
    url = entry.get("url")
    if isinstance(url, str):
        return posixpath.splitext(posixpath.basename(url))[0] or "api"
    raise Refused("an api entry with neither a name nor a url cannot be keyed")


def realm_relative(url):
    if REMOTE_URL.search(url):
        return url
    return posixpath.normpath(posixpath.join("apis", url))


def inverse_api(entry):
    spec = {}
    for key, value in entry.items():
        if key == "url" and isinstance(value, str):
            spec["url"] = realm_relative(value)
        elif key == "oauth2" and isinstance(value, dict):
            oauth = camel(value, OAUTH_TO_YAML | {"identity": "identity", "webhookTenancy": "webhook-tenancy"})
            if isinstance(oauth.get("identity"), dict):
                oauth["identity"] = camel(oauth["identity"], IDENTITY_TO_YAML)
            spec["oauth2"] = oauth
        else:
            spec[key] = value
    spec = camel(spec, API_TO_YAML)
    if not isinstance(entry.get("name"), str):
        spec["name"] = None
    else:
        del spec["name"]
    return spec


def inverse_webhook(entry):
    spec = {k: v for k, v in entry.items() if k != "name"}
    signature = spec.get("signature")
    if isinstance(signature, dict):
        signature = camel(signature, SIGNATURE_TO_YAML)
        if isinstance(signature.get("secretRef"), dict):
            signature["secretRef"] = camel(signature["secretRef"], {"oauthApp": "oauth-app"})
        if isinstance(signature.get("timestamp"), dict):
            signature["timestamp"] = camel(signature["timestamp"], {"toleranceSeconds": "tolerance-seconds"})
        spec["signature"] = signature
    return spec


def inverse_list_entry(kind, entry):
    if not isinstance(entry, dict):
        raise Refused(f"a {kind} entry is not a mapping")
    if kind == "apis":
        return api_key(entry), inverse_api(entry)
    if kind == "views":
        return None, entry
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise Refused(f"a {kind} entry has no name, and realm.ts keys every entry by name")
    if kind == "webhooks":
        return name, inverse_webhook(entry)
    return name, {k: v for k, v in entry.items() if k != "name"}


def inverse_file(kind, stem, doc):
    if kind == "events":
        if not isinstance(doc, list):
            raise Refused("an events file is not a list")
        events = []
        for event in doc:
            if not isinstance(event, dict):
                raise Refused("an event is not a mapping")
            copy = dict(event)
            if isinstance(copy.get("webhook"), dict):
                copy["webhook"] = camel(copy["webhook"], EVENT_WEBHOOK_TO_YAML)
            events.append(copy)
        return stem, events
    if not isinstance(doc, dict):
        raise Refused(f"a {kind} file is not a mapping")
    id_field = FILE_IDENTITY[kind][0]
    if kind == "channels":
        key = doc["name"] if isinstance(doc.get("name"), str) else stem
        return key, {k: v for k, v in camel(doc, CHANNEL_TO_YAML).items() if k != "name"}
    spec = dict(doc)
    if spec.get(id_field) == stem:
        del spec[id_field]
    for field, value in FILE_IDENTITY[kind][1].items():
        if spec.get(field) == value:
            del spec[field]
    return stem, spec


def with_defaults(kind, stem, doc):
    """A one-per-file document as its reader sees it: the identity and defaults it fills in."""
    if not isinstance(doc, dict):
        return doc
    id_field, defaults = FILE_IDENTITY[kind]
    out = dict(doc)
    if id_field and out.get(id_field) is None:
        out[id_field] = stem
    for field, value in defaults.items():
        if out.get(field) is None:
            out[field] = value
    return out


def first_difference(want, got, at=""):
    """Where two trees first differ, as a JSON-pointer-ish path and what each side holds."""
    if isinstance(want, dict) and isinstance(got, dict):
        for key in sorted(set(want) | set(got)):
            if key not in got:
                return f"{at}/{key}: the YAML holds it and realm.ts cannot carry it"
            if key not in want:
                return f"{at}/{key}: realm.ts would add it ({json.dumps(got[key])[:80]})"
            found = first_difference(want[key], got[key], f"{at}/{key}")
            if found:
                return found
        return None
    if isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            return f"{at}: {len(want)} entries in YAML, {len(got)} mapped back"
        for index, (left, right) in enumerate(zip(want, got)):
            found = first_difference(left, right, f"{at}/{index}")
            if found:
                return found
        return None
    if number(want) and number(got) and want == got:
        # JavaScript has one number type, so realm.ts cannot write 2.0 apart from 2.
        return None
    if want != got or type(want) is not type(got):
        return f"{at or '/'}: YAML {json.dumps(want)[:80]}, mapped back {json.dumps(got)[:80]}"
    return None


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def same(want, got):
    return first_difference(canonical(without_nulls(want)), canonical(without_nulls(got)))


# Comments -------------------------------------------------------------------------------------

def leading_comments(text):
    """The comment block at the head of a file, and the one above each top-level list entry."""
    lines = text.splitlines()
    head, entries, pending, started = [], [], [], False
    for line in lines:
        stripped = line.strip()
        if line.startswith("#"):
            pending.append(line[2:] if line[1:2] == " " else line[1:])
            continue
        if not stripped or stripped.startswith("#"):
            if not started and pending and not stripped:
                head.extend(pending + [""])
                pending = []
            continue
        if line.startswith("- ") or line == "-":
            entries.append(pending)
        elif not started:
            head.extend(pending)
        pending = []
        started = True
    while head and head[-1] == "":
        head.pop()
    return head, entries


# realm.ts text --------------------------------------------------------------------------------

IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


class Commented:
    """A value with a block comment to write above it."""

    def __init__(self, value, comment):
        self.value = value
        self.comment = comment


def ts_key(key):
    if key == "__proto__":
        return '["__proto__"]'
    return key if IDENTIFIER.match(key) else json.dumps(key, ensure_ascii=False)


def ts_string(value):
    if "\n" in value and not re.search(r"[`\\\r]|\$\{", value):
        return "`" + value + "`"
    return json.dumps(value, ensure_ascii=False)


def ts_comment(lines, pad):
    body = [line.replace("*/", "*\\/") for line in lines]
    if not any(body):
        return ""
    return pad + "/*\n" + "".join(f"{pad} *{(' ' + line) if line else ''}\n" for line in body) + pad + " */\n"


def ts(value, depth=0):
    pad, inner = "  " * depth, "  " * (depth + 1)
    if isinstance(value, Commented):
        return ts(value.value, depth)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise Refused("a number realm.ts cannot hold (NaN or infinity)")
        return json.dumps(value)
    if isinstance(value, str):
        return ts_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(item, (str, int, float, bool)) or item is None for item in value):
            line = "[" + ", ".join(ts(item) for item in value) + "]"
            if len(line) + len(pad) <= 100 and "\n" not in line:
                return line
        parts = []
        for item in value:
            comment = ts_comment(item.comment, inner) if isinstance(item, Commented) and item.comment else ""
            parts.append(f"{comment}{inner}{ts(item, depth + 1)},\n")
        return "[\n" + "".join(parts) + pad + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        parts = []
        for key, item in value.items():
            comment = ts_comment(item.comment, inner) if isinstance(item, Commented) and item.comment else ""
            parts.append(f"{comment}{inner}{ts_key(str(key))}: {ts(item, depth + 1)},\n")
        return "{\n" + "".join(parts) + pad + "}"
    raise Refused(f"a {type(value).__name__} value realm.ts cannot hold")


# One realm --------------------------------------------------------------------------------------

class Conversion:
    def __init__(self, source):
        self.source = source
        self.fields = {}
        self.converted = []
        self.gaps = []

    def gap(self, path, reason):
        self.gaps.append((path, reason))

    def field(self, name):
        return self.fields.setdefault(name, {} if name != "views" else [])


def realm_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "node_modules"))
        for filename in sorted(filenames):
            yield posixpath.relpath(posixpath.join(dirpath, filename), root).replace(os.sep, "/")


def is_yaml(path):
    return path.endswith((".yml", ".yaml"))


def stem_of(path):
    return posixpath.splitext(posixpath.basename(path))[0]


def is_api_document(doc):
    return isinstance(doc, dict) and ("openapi" in doc or "swagger" in doc or "info" in doc and "paths" in doc)


def convert_metadata(conv, path, doc):
    if not isinstance(doc, dict):
        conv.gap(path, "the realm metadata is not a mapping")
        return None
    unknown = [key for key in doc if key not in METADATA]
    if unknown:
        conv.gap(path, f"realm metadata field(s) {', '.join(unknown)} have no realm.ts field")
        return None
    if doc.get("host") == "docker":
        conv.gap(path, "host: docker; a realm.ts realm runs on the wasm host, so it cannot say docker")
        doc = {k: v for k, v in doc.items() if k != "host"}
    return doc


def convert_list_file(conv, kind, path, doc, text):
    if doc is None:
        conv.converted.append(path)
        return
    if not isinstance(doc, list):
        conv.gap(path, f"a {kind} file is not a list")
        return
    head, comments = leading_comments(text)
    staged = []
    try:
        for index, entry in enumerate(doc):
            key, spec = inverse_list_entry(kind, entry)
            if kind != "views":
                if key in conv.field(kind) or any(k == key for k, _, _ in staged):
                    raise Refused(f"{kind} {key!r} is declared twice, and realm.ts keys {kind} by name")
                problem = same(entry, map_list_entry(kind, key, spec))
                if problem:
                    raise Refused(f"{kind} {key!r}: {problem}")
            comment = (head + [""] if index == 0 and head else []) + (comments[index] if index < len(comments) else [])
            staged.append((key, spec, comment))
    except Refused as refused:
        conv.gap(path, str(refused))
        return
    for key, spec, comment in staged:
        item = Commented(spec, comment) if comment else spec
        if kind == "views":
            conv.field("views").append(item)
        else:
            conv.field(kind)[key] = item
    conv.converted.append(path)


def convert_one_file(conv, kind, path, doc, text):
    stem = stem_of(path)
    try:
        key, spec = inverse_file(kind, stem, doc)
        if key in conv.field(kind):
            raise Refused(f"{kind} {key!r} is declared twice")
        if kind == "events":
            want = doc
            got = map_file_kind(kind, key, spec)
        else:
            want = with_defaults(kind, stem, doc)
            got = map_file_kind(kind, key, spec)
        problem = same(want, got)
        if problem:
            raise Refused(problem)
    except Refused as refused:
        conv.gap(path, str(refused))
        return
    head, _ = leading_comments(text)
    conv.field(kind)[key] = Commented(spec, head) if head else spec
    conv.converted.append(path)


def convert_manifest(conv, path, doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        conv.gap(path, "the handler manifest has no list of entries")
        return
    extra = [key for key in doc if key not in ("version", "generatedAt", "entries", "entry")]
    if extra:
        conv.gap(path, f"manifest field(s) {', '.join(extra)} have no realm.ts field")
        return
    handlers = {}
    for entry in doc["entries"]:
        name = entry.get("name")
        unknown = [key for key in entry
                   if key not in ("namespace", "name", "description", "inputSchema", "outputSchema", "onType",
                                  "className", "schedule")]
        if unknown:
            conv.gap(path, f"handler {name!r} field(s) {', '.join(unknown)} have no realm.ts field")
            return
        if name in handlers:
            conv.gap(path, f"handler {name!r} is declared twice")
            return
        spec = {}
        for field, as_ in (("namespace", "namespace"), ("description", "description"), ("inputSchema", "input"),
                           ("outputSchema", "output"), ("onType", "onType"), ("className", "className"),
                           ("schedule", "schedule")):
            if entry.get(field) is not None:
                spec[as_] = entry[field]
        problem = same(entry, map_handler(name, spec))
        if problem:
            conv.gap(path, f"handler {name!r}: {problem}")
            return
        handlers[name] = spec
    conv.fields["handlers"] = handlers
    if doc.get("generatedAt") is not None:
        conv.fields["generatedAt"] = doc["generatedAt"]
    if doc.get("entry") is not None:
        conv.fields["entry"] = doc["entry"]
    conv.converted.append(path)


def derived_handlers(conv, root, entry, realm_name, bundler):
    """Names the handlers the way the host does when no manifest is committed: from the entry's text."""
    if not bundler:
        conv.gap(entry, "handlers are named from the source when no dist/manifest.json is committed; "
                        "pass --bundle-handlers so they are named as the host names them")
        return
    with tempfile.TemporaryDirectory() as scratch:
        shim = os.path.join(scratch, "shim.js")
        open(shim, "w").close()
        out = os.path.join(scratch, "out.js")
        run = subprocess.run(["node", bundler, os.path.join(root, entry), os.path.join(root, "wasm"), shim, out],
                             capture_output=True, text=True)
        if run.returncode != 0:
            conv.gap(entry, f"the host's bundling step refused the entry: {run.stderr.strip()[:200]}")
            return
        with open(os.path.join(scratch, "handlers.json"), encoding="utf-8") as handle:
            found = json.load(handle)
    namespace = realm_name.replace("-", "_")
    handlers = {}
    for name in found.get("handlers", []):
        spec = {"namespace": namespace, "description": f"The {name} handler of the {realm_name} realm.", "input": {}}
        if name in found.get("schedules", {}):
            spec["schedule"] = found["schedules"][name]
        handlers[name] = spec
    conv.fields["handlers"] = handlers
    conv.gap(entry, "NOTE handlers named from the source carry no output schema; realm.ts declares "
                    "{\"type\": \"object\"} for each, which a handler returning a non-object would fail")


def convert(source, out, bundler=None):
    """Writes the twin of the realm at source to out, and returns the Conversion."""
    conv = Conversion(source)
    if os.path.exists(os.path.join(source, "realm.ts")):
        conv.gap("realm.ts", "the realm already has a realm.ts; copied as it is")
        shutil.copytree(source, out, ignore=shutil.ignore_patterns(".git"), dirs_exist_ok=True)
        return conv
    files = list(realm_files(source))
    meta_path = "realm.yml" if "realm.yml" in files else "pack.yml" if "pack.yml" in files else None
    if meta_path is None:
        conv.gap(".", "no realm.yml or pack.yml: not a realm")
        return conv
    raw_metadata = read_yaml(os.path.join(source, meta_path))
    metadata = convert_metadata(conv, meta_path, raw_metadata)
    if metadata is None:
        return conv
    realm_name = metadata.get("name")
    for path in files:
        if path in (meta_path, "pack.yml", "realm.yml"):
            continue
        top = path.split("/")[0]
        kind = top if "/" in path else None
        full = os.path.join(source, path)
        if path in NO_FIELD or kind in NO_FIELD:
            conv.gap(path, NO_FIELD.get(path) or NO_FIELD[kind])
            continue
        if kind in LIST_KINDS + FILE_KINDS and path.count("/") == 1 and is_yaml(path):
            try:
                if kind == "apis" and is_api_document(read_yaml(full, aliases=True)):
                    continue
                doc = read_yaml(full)
            except (yaml.YAMLError, ValueError) as failed:
                conv.gap(path, f"does not parse: {str(failed).splitlines()[0]}")
                continue
            with open(full, encoding="utf-8") as handle:
                text = handle.read()
            if kind in LIST_KINDS:
                convert_list_file(conv, kind, path, doc, text)
            else:
                convert_one_file(conv, kind, path, doc, text)
            continue
        if path == "dist/manifest.json":
            host = raw_metadata.get("host") or ("wasm" if any(f.startswith("wasm/") for f in files) else "docker")
            if host != "wasm":
                conv.gap(path, "handlers run on the docker (Node bundle) host, which a realm.ts realm cannot use")
                continue
            with open(full, encoding="utf-8") as handle:
                convert_manifest(conv, path, json.load(handle))
            continue
        if path == "dependencies/manifest.json":
            conv.gap(path, "a committed dependencies manifest is not carried; declare `dependencies` by hand")
            continue
        if not is_yaml(path) or path in RESOURCE_FILES or top in RESOURCES:
            continue
        if "/" not in path:
            conv.gap(path, "a top-level YAML file this converter does not know as a declaration")
        elif top not in LIST_KINDS + FILE_KINDS:
            conv.gap(path, f"{top}/ is not a declaration folder this converter knows")
        elif top != "apis":
            # apis/ nests vendored documents; any other kind's reader reads only its own folder.
            conv.gap(path, f"a YAML file below {top}/, which the converter does not read as a declaration")
    wasm_entry = next((e for e in ("wasm/handlers.ts", "wasm/handlers.js") if e in files), None)
    if wasm_entry and "handlers" not in conv.fields and "dist/manifest.json" not in files:
        derived_handlers(conv, source, wasm_entry, realm_name, bundler)
    if wasm_entry == "wasm/handlers.js" and "entry" not in conv.fields:
        conv.fields["entry"] = wasm_entry

    definition = {key: metadata[key] for key in METADATA if key in metadata}
    for key in ("entry", "generatedAt", "handlers", *LIST_KINDS, *FILE_KINDS):
        if conv.fields.get(key):
            definition[key] = conv.fields[key]
    shutil.copytree(source, out, ignore=shutil.ignore_patterns(".git"), dirs_exist_ok=True)
    with open(os.path.join(out, "realm.ts"), "w", encoding="utf-8") as handle:
        handle.write(render(definition, conv))
    for path in [meta_path, *conv.converted]:
        os.remove(os.path.join(out, path))
    for kind in LIST_KINDS + FILE_KINDS:
        folder = os.path.join(out, kind)
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    return conv


def render(definition, conv):
    header = [
        "This realm.ts was converted from the YAML realm by realm-yml-to-ts.py. It declares what the",
        "realm's YAML files declared, and maps back to the same documents.",
    ]
    kept = {}
    for path, reason in conv.gaps:
        if not reason.startswith("NOTE"):
            where = path.split("/")[0] + "/" if "/" in path else path
            kept[where] = kept.get(where, 0) + 1
    if kept:
        header += ["", "These stay files, because they could not be carried (the converter printed why):", ""]
        header += [f"  {where}" + (f" ({count} files)" if count > 1 else "") for where, count in sorted(kept.items())]
    return ts_comment(header, "") + "export default " + ts(definition) + ";\n"


# Verify -----------------------------------------------------------------------------------------

def evaluate(realm_ts):
    """The value realm.ts evaluates to, by Node, which erases its types and runs it."""
    script = ("import(process.argv[1]).then(m => process.stdout.write(JSON.stringify(m.default)))"
              ".catch(e => { console.error(String(e)); process.exit(2) })")
    run = subprocess.run(["node", "--input-type=module", "-e", script, "--", os.path.abspath(realm_ts)],
                         capture_output=True, text=True)
    if run.returncode != 0:
        raise Refused(f"realm.ts does not evaluate: {run.stderr.strip()[:300]}")
    return json.loads(run.stdout), len(run.stdout.encode("utf-8"))


EVALUATION_OUTPUT_LIMIT = 1 << 20


def verify(twin, original):
    """
    Checks a twin against its original: every YAML file the twin dropped must come back, from the
    evaluated realm.ts, as the same document. Returns (equal, first difference or None, notes).
    """
    definition, size = evaluate(os.path.join(twin, "realm.ts"))
    notes = []
    if size > EVALUATION_OUTPUT_LIMIT:
        notes.append(f"realm.ts evaluates to {size} bytes, over the host's 1 MiB evaluation output limit")
    mapped = map_definition(definition)
    original_files = set(realm_files(original))
    dropped = sorted(path for path in original_files - set(realm_files(twin)))
    meta = "realm.yml" if "realm.yml" in original_files else "pack.yml"
    expected_meta = read_yaml(os.path.join(original, meta))
    if expected_meta.get("host") == "docker":
        expected_meta = {k: v for k, v in expected_meta.items() if k != "host"}
    problem = same(expected_meta, mapped["realm.yml"])
    if problem:
        return False, f"{meta}: {problem}", notes
    by_kind = {}
    for path in dropped:
        kind = path.split("/")[0]
        if kind in LIST_KINDS:
            by_kind.setdefault(kind, []).extend(read_yaml(os.path.join(original, path)) or [])
        elif kind in FILE_KINDS:
            got = mapped.get(f"{kind}/{stem_of(path)}.yml")
            doc = read_yaml(os.path.join(original, path))
            if kind == "channels":
                got = mapped.get(f"channels/{doc.get('name') or stem_of(path)}.yml")
            want = doc if kind == "events" else with_defaults(kind, stem_of(path), doc)
            if got is None:
                return False, f"{path}: realm.ts maps nothing back to it", notes
            problem = same(want, got)
            if problem:
                return False, f"{path}: {problem}", notes
    for kind, entries in by_kind.items():
        path = "apis/apis.yml" if kind == "apis" else f"{kind}/{definition['name']}.yml"
        got = mapped.get(path, [])
        if kind == "views":
            problem = same(entries, got)
        else:
            want = {api_key(e) if kind == "apis" else e["name"]: e for e in entries}
            have = {api_key(e) if kind == "apis" else e["name"]: e for e in got}
            problem = same(want, have)
        if problem:
            return False, f"{kind}: {problem}", notes
    if "dist/manifest.json" in dropped:
        with open(os.path.join(original, "dist/manifest.json"), encoding="utf-8") as handle:
            want = json.load(handle)
        got = mapped.get("dist/manifest.json")
        problem = same({e["name"]: e for e in want["entries"]}, {e["name"]: e for e in (got or {}).get("entries", [])})
        if problem:
            return False, f"dist/manifest.json: {problem}", notes
    return True, None, notes


# Command line -----------------------------------------------------------------------------------

def report(name, conv):
    for path, reason in conv.gaps:
        if reason.startswith("NOTE"):
            print(f"NOTE {name}/{path}: {reason[5:]}")
        else:
            print(f"NOT CONVERTED {name}/{path}: {reason}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--all", action="store_true", help="convert every realm directory under the first path")
    parser.add_argument("--verify", action="store_true", help="check a converted realm against its original")
    parser.add_argument("--bundle-handlers", help="the host's bundle-handlers.mjs, to name source-only handlers")
    parser.add_argument("--report", help="write a JSON summary of each realm's conversion here")
    parser.add_argument("source")
    parser.add_argument("out")
    args = parser.parse_args(argv)

    if args.verify:
        if os.path.exists(os.path.join(args.out, "realm.ts")):
            print("mapping-equal: n/a (the original is already a realm.ts realm)")
            return 0
        try:
            equal, difference, notes = verify(args.source, args.out)
        except Refused as refused:
            equal, difference, notes = False, str(refused), []
        for note in notes:
            print(f"NOTE {note}")
        print("mapping-equal: yes" if equal else f"mapping-equal: no ({difference})")
        return 0 if equal else 1

    if args.all:
        sources = [os.path.join(args.source, d) for d in sorted(os.listdir(args.source))
                   if os.path.isdir(os.path.join(args.source, d)) and not d.startswith(".")]
    else:
        sources = [args.source]
    summary = {}
    for source in sources:
        name = os.path.basename(os.path.normpath(source))
        out = os.path.join(args.out, name) if args.all else args.out
        if os.path.exists(out):
            shutil.rmtree(out)
        conv = convert(source, out, args.bundle_handlers)
        report(name, conv)
        summary[name] = {
            "converted": sorted(conv.converted),
            "notConverted": [{"path": p, "reason": r} for p, r in conv.gaps if not r.startswith("NOTE")],
            "notes": [{"path": p, "reason": r[5:]} for p, r in conv.gaps if r.startswith("NOTE")],
            "written": os.path.exists(os.path.join(out, "realm.ts")),
        }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
