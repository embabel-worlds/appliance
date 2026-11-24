"""Who owns a type a realm extends: the realms it `requires`, found among the checkouts beside it.

A realm that requires another may write an entry for a label the other declares, adding properties
or joins. Declarations of one label accumulate on the host, so that entry is an extension, not a
second declaration: the owner's description and identity are the ones a model sees. Restating them
in the extension would load a second declaration of the type and make the realm worse, so the audit
must not ask for them there. It asks the owner instead, by reading the owner's own declaration.

The owner is found where the operator keeps realm checkouts, the way `embabel realm audit <dir>`
already reads them: the directory the audited realm sits in (`realm-<name>` beside it, or `<name>`,
or any checkout whose realm.yml declares that name), then EMBABEL_REALMS_DIR when it is set in the
environment. The audit reads checkouts and no appliance's settings, so it does not read an
instance's .env for that directory. Requirements are followed transitively: a realm may extend a
type that a realm it requires itself takes from a third.

A requirement that is not checked out is said, never guessed: an entry that looks like an extension
(no description, no identity) in a realm with an unreadable requirement is skipped for the type and
property rules, with a note that the owner's declaration could not be checked.
"""

from __future__ import annotations
import os
import re

from .realmsource import RealmSourceError, read_realm, read_yaml

# Working copies beside a realm (`-fix…`, spikes, drafts) are somebody's work in progress: never the
# realm a requirement names, and never scored as a realm of their own.
SKIP = re.compile(r"-fix|spike|draft", re.IGNORECASE)
REALM_FILES = ("realm.ts", "realm.yml", "realm.yaml", "pack.yml")


def is_realm(path: str) -> bool:
    return any(os.path.isfile(os.path.join(path, f)) for f in REALM_FILES)


def declared_name(root: str):
    """realm.yml's name, cheaply; a realm.ts realm is known by its directory alone."""
    path = os.path.join(root, "realm.yml")
    if not os.path.isfile(path):
        return None
    try:
        doc = read_yaml(path)
    except Exception:  # an unreadable realm.yml is reported when the realm is audited
        return None
    return doc.get("name") if isinstance(doc, dict) else None


def required_names(realm: dict) -> list:
    """`requires: {name: range}` as the host reads it; a list of names, or of {name:}, reads the same."""
    req = realm.get("requires")
    if isinstance(req, dict):
        return [str(n) for n in req]
    if isinstance(req, list):
        return [str(r.get("name")) if isinstance(r, dict) else str(r) for r in req
                if (r.get("name") if isinstance(r, dict) else r)]
    return []


def _search_dirs(near: str) -> list:
    dirs = [os.path.dirname(os.path.abspath(near))]
    linked = os.environ.get("EMBABEL_REALMS_DIR")
    if linked:
        dirs.append(os.path.realpath(os.path.expanduser(linked)))
    out = []
    for d in dirs:
        if os.path.isdir(d) and os.path.realpath(d) not in (os.path.realpath(x) for x in out):
            out.append(d)
    return out


def locate(name: str, near: str):
    """The checkout of realm `name`, beside `near` or under EMBABEL_REALMS_DIR, or None."""
    for d in _search_dirs(near):
        for candidate in (f"realm-{name}", name):
            path = os.path.join(d, candidate)
            if os.path.isdir(path) and is_realm(path) and os.path.realpath(path) != os.path.realpath(near):
                return path
        for entry in sorted(os.listdir(d)):
            path = os.path.join(d, entry)
            if entry.startswith(".") or SKIP.search(entry) or not os.path.isdir(path) \
                    or os.path.realpath(path) == os.path.realpath(near):
                continue
            if declared_name(path) == name:
                return path
    return None


def declares(entry: dict) -> bool:
    """An entry that states what the type is, rather than only adding to it."""
    props = entry.get("properties")
    identity = isinstance(props, (dict, list)) and any(
        isinstance(spec, dict) and (spec.get("identity") in (True, "true")
                                    or (isinstance(spec.get("metadata"), dict) and spec["metadata"].get("identity") in (True, "true")))
        for spec in (props.values() if isinstance(props, dict) else props))
    return bool(str(entry.get("description") or "").strip() or identity or entry.get("population") or entry.get("spine"))


class Owners:
    """The labels this realm's requirements declare, and the realm whose declaration stands for each."""

    def __init__(self, root: str, realm: dict):
        self.realms = {}        # name -> (path, realm read)
        self.deps = {}          # name -> names it requires
        self.unavailable = {}   # name -> why it could not be read
        queue = [(n, root) for n in required_names(realm)]
        while queue:
            name, near = queue.pop(0)
            if name in self.realms or name in self.unavailable:
                continue
            path = locate(name, near)
            if not path:
                self.unavailable[name] = "not checked out beside this realm or under EMBABEL_REALMS_DIR"
                continue
            try:
                required = read_realm(path)
            except RealmSourceError as e:
                self.unavailable[name] = str(e)
                continue
            self.realms[name] = (path, required)
            self.deps[name] = required_names(required)
            queue += [(n, path) for n in self.deps[name]]

    def _closure(self, name: str) -> set:
        seen, stack = set(), list(self.deps.get(name, []))
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                stack += self.deps.get(n, [])
        return seen

    def properties(self, label: str) -> set:
        """Every property name a requirement gives the label, by declaration or by extension."""
        names = set()
        for _, r in self.realms.values():
            for t in r.get("types") or []:
                if t.get("name") == label:
                    props = t.get("properties")
                    if isinstance(props, dict):
                        names |= set(props)
                    elif isinstance(props, list):
                        names |= {p.get("name") for p in props if isinstance(p, dict) and p.get("name")}
        return names

    def owner(self, label: str):
        """(owner name, path, its entry for the label) for a label a requirement holds, else None.

        The owner is the realm that holds the label and takes it from none of its own requirements;
        a realm in between that only extends it is passed over. Its entry is checked whatever it
        says, since a deficient owner declaration is exactly what the owner should hear about.
        """
        holders = [n for n, (_, r) in self.realms.items()
                   if any(t.get("name") == label for t in r.get("types") or [])]
        if not holders:
            return None
        roots = [n for n in holders if not (self._closure(n) & set(holders))] or holders
        name = roots[0]
        entries = [t for t in self.realms[name][1].get("types") or [] if t.get("name") == label]
        return name, self.realms[name][0], next((t for t in entries if declares(t)), entries[0])
