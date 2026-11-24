"""`embabel realm audit <path>`: how far a realm is from realm-spec REALM_QUALITY.md, read from its files.

The standard asks for metadata a model can answer from and producers that ask their source for as
little as the question needs. Most of that is a judgement a reviewer makes. Some of it is visible in
the declarations — a type with no description, a status with no listed values, a producer that never
says what it can push, a view whose name match is partial and does not say so — and those are what
this scores. A realm can pass every check here and still describe itself badly; it cannot fail one
and describe itself well.

THE CHECK IDS ARE THE STANDARD'S (T1, P2, R3, …), so a finding names the rule it breaks and the
rule says why it matters. The score is a weighted mean over the rules that apply, MUST weighing
three times a SHOULD, each rule scored by the share of its declarations that meet it:

    score = 100 * sum(weight * (1 - failed / checked)) / sum(weight)

A realm with nothing a rule applies to (no types, no producers, no views) has no score, not 100.

HEURISTICS SAY SO. A unit on a field named `amount`, a list of values on one named `status`, a by-id
endpoint beside a list-only producer: each is a guess from a name or an API document, and the
finding reads "looks like", never "is". The fix for a false positive is a better description, which
is never wasted.

Nothing is fetched and nothing needs to be running: the realm is read the host's way
(realmsource.py), and a vendored OpenAPI document under apis/ is the only view of the source it has.
"""

from __future__ import annotations
import json
import math
import os
import re

from .realmsource import RealmSourceError, api_documents, read_realm, read_yaml

GUIDE_CUT = 320          # the query guide shows a type's description up to here, and no further
PROPERTY_CUT = 160       # a property's description, when the question names it
VIEW_LISTING_CUT = 80    # a view's first sentence in the view listing a model chooses from (60 when crowded)
REALM_INDEX_CUT = 120    # a realm's first sentence in the query guide's index
MUST, SHOULD = "MUST", "SHOULD"
WEIGHT = {MUST: 3, SHOULD: 1}

# The rule each check id stands for, as the standard words it. Kept short: the standard has the why.
RULES = {
    "T1": (MUST, "every type has a description"),
    "T2": (MUST, "a type's first 320 characters say what one node is and what identifies it"),
    "T3": (MUST, "a type reached by more than one join, populated, or a spine, declares its identity"),
    "T5": (SHOULD, "every other fetched type declares its identity"),
    "P1": (MUST, "every property has a description that says more than its name"),
    "P2": (SHOULD, "a money or measure property states its currency or unit"),
    "P3": (SHOULD, "a status-like property lists its values"),
    "P4": (SHOULD, "a date or time property says which moment it is and how it is written"),
    "P5": (SHOULD, "a numeric or boolean property declares its type"),
    "K1": (SHOULD, "a cross-system key (domain, email, account key) is declared as one"),
    "J1": (MUST, "every join has a description, written for the question that traverses it"),
    "R1": (MUST, "a producer that can filter at its source declares pushdown (rules, or [] for none)"),
    "R2": (MUST, "a producer that reads a remote source declares a cache"),
    "R3": (SHOULD, "a keyed producer's cache remembers a clean miss (negativeTtlSeconds)"),
    "R4": (SHOULD, "a paged producer states its page size and page cap"),
    "R5": (SHOULD, "the page size is the source's maximum"),
    "R6": (SHOULD, "a source that reports a total is counted at the source (aggregates: count)"),
    "R7": (SHOULD, "a single record is read through the source's by-id endpoint, not a listing"),
    "V1": (MUST, "every view has a description saying what it answers"),
    "V2": (MUST, "every view param has a type and a description"),
    "V3": (MUST, "a param matched partially (CONTAINS, STARTS WITH, =~) says it is partial"),
    "V4": (SHOULD, "every view param has a default, or states whether it is required"),
    "V5": (SHOULD, "a view's description says what it does not answer, or how it is bounded"),
    "V6": (SHOULD, "a view's name says what it counts"),
    "R8": (SHOULD, "a remote pushdown rule states its op (left out, it is CONTAINS)"),
    "V7": (SHOULD, "a view's first sentence says what it answers in 80 characters"),
    "M1": (MUST, "realm.yml has a description"),
    "M2": (SHOULD, "the realm's first sentence says what it holds in 120 characters"),
    "X1": (SHOULD, "the realm ships worked Cypher examples over its main joins"),
    "Z1": (MUST, "nothing is declared that the host ignores"),
}

# What the host reads on a property. Anything else on one — `unit:`, `currency:`, `format:`,
# `valuePattern:`, `default:` — is dropped without a word, so it is a promise nothing keeps.
PROPERTY_KEYS = {"name", "description", "type", "cardinality", "validation", "values", "enum", "metadata",
                 "identity", "required", "annotatable", "annotationVersion", "joinKey", "hub"}
CACHE_KINDS = {"none", "fresh", "ttl", "immutable", "graph", "session"}
GRAPH_CACHE_KINDS = {"aggregate", "extract"}

# Kinds that fetch from somewhere a cache saves a call to, and that take pushdown rules.
REMOTE_KINDS = {"remote", "api"}
PUSHDOWN_KINDS = REMOTE_KINDS | {"sql", "sparql", "cypher", "tabular", "feed", "elasticsearch", "elastic", "publisher"}
CACHED_KINDS = REMOTE_KINDS | {"sql", "sparql", "cypher", "graphql", "feed", "elasticsearch", "elastic",
                               "remote-search", "publisher"}

MONEY = re.compile(r"(?i)(amount|price|cost|revenue|balance|fee|mrr|arr|cents|salary|income|spend|budget|"
                   r"(?<![a-z])value$|total(?!count)|subtotal|tax|discount|refund|paid|due$)")
MEASURE = re.compile(r"(?i)(duration|latency|elapsed|bytes|weight|distance|height|length|width|depth|"
                     r"temperature|seconds|minutes|hours|millis|ms$|_ms$|size$|area|volume|speed|"
                     r"(?<![a-z])age$|percent|ratio)")
UNIT_WORDS = re.compile(r"(?i)((?-i:\b[A-Z]{3}\b)|currency|cents|minor units|major units|dollars|pounds|euros|"
                        r"[$£€¥]|\bunits?\b|seconds|minutes|hours|days|millis|\bms\b|bytes|\b[kmg]i?b\b|"
                        r"metres|meters|\bkm\b|\bm\b|miles|kg|grams|\bg\b|celsius|fahrenheit|°|"
                        r"percent|%|fraction|ratio|0\s*(to|-|–|\.\.)\s*1|per\s)")
ENUMISH = re.compile(r"(?i)(^|[a-z])(status|state|stage|priority|severity|kind|category|level|tier|phase|"
                     r"outcome|conclusion|result|direction|visibility|role)$|^type$")
# Case matters: `openedOn` is a date and `location` is not.
DATEISH = re.compile(r"(?:On|At|Date|Time|Since|_at|_on|_date|_time)$|^(?:date|time|created|updated|modified|"
                     r"timestamp|when|since)$|_date_|[Dd]ate[A-Z_]")
DATE_WORDS = re.compile(r"(?i)(iso|yyyy|epoch|utc|unix|date-?time|timestamp|\bday\b|\bdate\b|"
                        r"time ?zone|local time|seconds since|milliseconds since)")
# Case matters here: `isPaid` is a flag and `issuedOn` is not, `numSeats` a count and `name` not.
NUMERIC_NAME = re.compile(r"(?:[Cc]ount|[Tt]otal|[Qq]ty|[Qq]uantity|[Ss]core|[Dd]ays|[Hh]ours|[Mm]inutes|[Ss]econds|"
                          r"[Pp]ercent|[Rr]ate)$|^(?:is|has|can|was)[A-Z]|^num[A-Z_]|^(?:count|total|days)\b")
CROSS_KEY = re.compile(r"(?i)^(email|domain|website|accountKey|companyDomain|emailDomain)$")
PARTIAL_WORDS = re.compile(r"(?i)(partial|contain|substring|fragment|part of|anywhere in|starts with|"
                           r"prefix|suffix|regex|pattern|fuzzy|case-insensitive|matches any)")
BOUND_WORDS = re.compile(r"(?i)(\bnot\b|\bonly\b|never|exclud|without|except|\blimit|\btop\b|\bfirst\b|"
                         r"at most|up to|bounded|capped|does not|doesn't|cannot|isn't|rather than|instead)")
PARTIAL_USE = re.compile(r"(?is)(CONTAINS|STARTS\s+WITH|ENDS\s+WITH|=~)\s*((?:[\w.]+\s*\(\s*)*\$(\w+))")


def _norm_props(props) -> dict:
    """{name: {description, ...}} from either spelling: a string value is the description."""
    out = {}
    if isinstance(props, dict):
        for name, spec in props.items():
            if isinstance(spec, dict):
                out[name] = spec
            elif isinstance(spec, str):
                out[name] = {"description": spec}
            else:
                out[name] = {}
    elif isinstance(props, list):
        for spec in props:
            if isinstance(spec, dict) and spec.get("name"):
                out[spec["name"]] = spec
    return out


def _is_identity(spec: dict) -> bool:
    meta = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
    return spec.get("identity") in (True, "true") or meta.get("identity") in (True, "true")


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _words(name: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").lower()


FILLER = {"the", "a", "an", "its", "it", "of", "this", "is", "s"}


def _describes(name: str, description: str) -> bool:
    """More than the name said again: `status: "The status."` tells a model nothing it did not have."""
    said = set(re.findall(r"[a-z0-9]+", description.lower())) - FILLER
    return len(description) >= 10 and not said <= set(_words(name).split())


def _params(view: dict) -> dict:
    return _norm_props(view.get("params"))


class Audit:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.checked = {}   # id -> count applied
        self.failed = {}    # id -> count failed
        self.findings = []

    def apply(self, cid: str, ok: bool, where: str, message: str = ""):
        self.checked[cid] = self.checked.get(cid, 0) + 1
        if not ok:
            self.failed[cid] = self.failed.get(cid, 0) + 1
            level, rule = RULES[cid]
            self.findings.append({"id": cid, "level": level, "where": where, "message": message or rule})

    def score(self):
        """Each rule once, by the share of its declarations that meet it; MUST rules weigh three.

        Per rule rather than per declaration, so two hundred well-described properties cannot
        buy back a realm whose views say nothing.
        """
        total = sum(WEIGHT[RULES[c][0]] for c in self.checked)
        if not total:
            return None
        met = sum(WEIGHT[RULES[c][0]] * (1 - self.failed.get(c, 0) / n) for c, n in self.checked.items())
        return round(100 * met / total)


def _check_types(a: Audit, realm: dict):
    for t in realm.get("types") or []:
        name = t.get("name") or "?"
        where = f"{t.get('_file', '')} {name}".strip()
        props = _norm_props(t.get("properties"))
        description = _text(t.get("description"))
        joins = [j for j in (t.get("virtualJoins") or []) if isinstance(j, dict)]
        fetched = bool(joins or t.get("population") or t.get("spine"))
        identity = [p for p, spec in props.items() if _is_identity(spec)]
        if isinstance(t.get("spine"), dict) and t["spine"].get("key"):
            identity.append(t["spine"]["key"])

        # A description that only says the name again is dropped before a model sees it.
        a.apply("T1", len(description) >= 40 and _describes(name, description), where,
                "no description" if not description else f"description {description[:60]!r} says too little to steer by")
        if description:
            head = description[:GUIDE_CUT].lower()
            names_one = re.search(r"(?i)^(a|an|one|each|the)\b|\b(one|each|a single|every)\b", head) is not None
            names_key = (not identity or any(re.search(rf"\b{re.escape(i.lower())}\b", head) for i in identity)
                         or re.search(r"\b(keyed|identified|unique|by its|by their|\bid\b|key)\b", head) is not None)
            missing = [w for w, ok in (("what one node is", names_one), ("what identifies it", names_key)) if not ok]
            cut = f" (it runs to {len(description)}; the guide stops at {GUIDE_CUT})" if len(description) > GUIDE_CUT else ""
            a.apply("T2", not missing, where, f"the first {GUIDE_CUT} characters do not say {' or '.join(missing)}{cut}")
        # Identity is what makes two paths to one record one node. Where two paths exist (several joins,
        # a population, a spine) it is required; on a single join it is still what a cache and a
        # model key a record by.
        if fetched and not t.get("projected"):
            must = len(joins) > 1 or bool(t.get("population") or t.get("spine"))
            a.apply("T3" if must else "T5", bool(identity), where,
                    "reached by more than one path and declares no identity property" if must
                    else "fetched through a join and declares no identity property")

        for pname, spec in props.items():
            pwhere = f"{where}.{pname}"
            ignored = sorted(k for k in spec if k not in PROPERTY_KEYS and not k.startswith("_"))
            if ignored:
                a.apply("Z1", False, pwhere, f"{', '.join(ignored)} on a property is dropped by the host; "
                        "say it in the description (a unit or format), or on a pushdown rule (valuePattern)")
            else:
                a.apply("Z1", True, pwhere)
            pdesc = _text(spec.get("description"))
            a.apply("P1", _describes(pname, pdesc), pwhere,
                    "no description" if not pdesc else f"description {pdesc!r} says little more than the name")
            ptype = str(spec.get("type") or "").lower()
            has_values = bool(spec.get("values") or spec.get("enum") or
                              (isinstance(spec.get("validation"), list) and any(
                                  isinstance(v, dict) and v.get("rule") == "oneOf" for v in spec["validation"])))
            text = f"{pdesc} {json.dumps(spec.get('metadata') or {})}"
            if (MONEY.search(pname) or MEASURE.search(pname)) and not ptype.startswith("bool") \
                    and not DATEISH.search(pname) and not DATEISH.search(pname):
                a.apply("P2", UNIT_WORDS.search(pdesc) is not None,
                        pwhere, "looks like money or a measure, and no currency or unit is stated")
            if ENUMISH.search(pname) and ptype not in ("int", "integer", "number", "boolean", "long", "float"):
                listed = has_values or len(re.findall(r"'[^']+'|\"[^\"]+\"|`[^`]+`", pdesc)) >= 2 \
                    or re.search(r"\w+\s*,\s*\w+.*\bor\b|\w+\s*[/|]\s*\w+\s*[/|]", pdesc) is not None
                a.apply("P3", listed, pwhere, "looks like an enumeration, and lists no values")
            if DATEISH.search(pname):
                a.apply("P4", DATE_WORDS.search(text) is not None and len(pdesc) >= 20, pwhere,
                        "a date or time with no word on which moment it is or how it is written (ISO, epoch, UTC, day)")
            if NUMERIC_NAME.search(pname):
                a.apply("P5", bool(ptype), pwhere,
                        "looks numeric or boolean and declares no type, so the source's encoding (often a string) is kept")
            if CROSS_KEY.match(pname) and fetched and not t.get("spine"):
                a.apply("K1", bool(spec.get("hub") or spec.get("joinKey") or spec.get("spine")), pwhere,
                        "a key other systems hold too, with no hub: or joinKey: to join on it")

        for j in joins:
            jwhere = f"{where} <-[:{j.get('relationship', '?')}]- {j.get('anchorLabel', '?')}"
            jd = _text(j.get("description"))
            a.apply("J1", len(jd) >= 30, jwhere, "no description" if not jd else f"description {jd!r} is too short to steer by")


def _operations(apis: dict) -> dict:
    """{operationId: (path, method, operation)} across every vendored OpenAPI document."""
    ops = {}
    for doc in apis.values():
        for path, item in (doc.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            for method, op in item.items():
                if isinstance(op, dict) and op.get("operationId"):
                    ops[op["operationId"]] = (path, method.lower(), op)
    return ops


def _size_max(op: dict, size_param: str):
    for p in op.get("parameters") or []:
        if isinstance(p, dict) and p.get("name") == size_param:
            schema = p.get("schema") if isinstance(p.get("schema"), dict) else p
            m = schema.get("maximum")
            return m if isinstance(m, (int, float)) else None
    return None


def _check_producers(a: Audit, realm: dict, apis: dict):
    ops = _operations(apis)
    used_ops = {str(p.get("operation")) for p in realm.get("producers") or []}
    for p in realm.get("producers") or []:
        kind = str(p.get("kind") or "").lower()
        where = f"{p.get('_file', '')} {p.get('name', '?')}".strip()
        keyed = any(p.get(k) for k in ("keyArg", "keyArgs", "keyTemplate", "keyVar", "keySplit")) \
            or "{keys}" in json.dumps(p.get("args") or {}) or ":keys" in str(p.get("query") or "") \
            or "{{keys}}" in str(p.get("query") or "")
        if kind in PUSHDOWN_KINDS:
            a.apply("R1", "pushdown" in p or p.get("readWhole") is True, where,
                    "declares no pushdown:, so every WHERE on it is applied after the fetch; list the filters the "
                    "source takes, or pushdown: [] when it takes none")
        cache = p.get("cache") if isinstance(p.get("cache"), dict) else None
        if cache is not None:
            ckind = str(cache.get("kind", "")).lower()
            wrong = []
            if ckind not in CACHE_KINDS:
                wrong.append(f"cache kind {ckind or '(none given)'!r} is not one the host knows")
            if ckind == "ttl" and "seconds" not in cache:
                wrong.append("a ttl cache with no `seconds`" + (" (`ttlSeconds` is not the field)" if "ttlSeconds" in cache else ""))
            if ckind == "session":
                wrong.append("cache kind session is accepted and not honoured; it reads as none")
            if ckind == "graph" and kind not in GRAPH_CACHE_KINDS:
                wrong.append(f"cache kind graph is honoured only by aggregate and extract producers; on {kind or 'this kind'} it reads as none")
            cost = p.get("cost") if isinstance(p.get("cost"), dict) else {}
            if "latencyMs" in cost:
                wrong.append("cost.latencyMs is read by nothing")
            a.apply("Z1", not wrong, where, "; ".join(wrong))
        if kind in REMOTE_KINDS:
            for rule in p.get("pushdown") or []:
                if isinstance(rule, dict):
                    a.apply("R8", "op" in rule, f"{where} pushdown {rule.get('property', '?')}",
                            "no op: a remote rule without one is CONTAINS, which an equality does not ask for")
        if kind in CACHED_KINDS:
            a.apply("R2", cache is not None, where, "no cache: every query asks the source again")
            if cache and keyed and str(cache.get("kind", "ttl")) == "ttl":
                a.apply("R3", cache.get("negativeTtlSeconds") is not None, where,
                        "keyed, and a key with no records is asked again on every query (no negativeTtlSeconds)")
        paging = p.get("paging") if isinstance(p.get("paging"), dict) else None
        if paging is not None:
            defaults = {"size": "100 per page", "maxPages": "5 pages"}
            missing = [f for f in defaults if f not in paging]
            a.apply("R4", not missing, where,
                    "paging leaves " + " and ".join(f"{f} at its default ({defaults[f]})" for f in missing))
            if paging.get("total") is not None:
                aggs = p.get("aggregates") or []
                a.apply("R6", any(isinstance(g, dict) and str(g.get("measure")).lower() == "count" for g in aggs), where,
                        "the source reports a total on every page, and a count still walks the pages (declare aggregates: count)")
        op = ops.get(str(p.get("operation")))
        if op and kind in REMOTE_KINDS:
            path, method, operation = op
            if paging is not None:
                mx = _size_max(operation, paging.get("sizeParam", "per_page"))
                if mx is not None:
                    a.apply("R5", paging.get("size") is not None and paging.get("size") >= mx, where,
                            f"page size {paging.get('size', 100)} where the API document allows {int(mx)}")
            if method == "get" and not re.search(r"\{[^}]+\}/?$", path):
                by_id = [oid for oid, (pp, mm, _) in ops.items()
                         if mm == "get" and re.fullmatch(re.escape(path.rstrip("/")) + r"/\{[^}/]+\}", pp)]
                if by_id:
                    a.apply("R7", any(oid in used_ops for oid in by_id), where,
                            f"lists {path}; the API document has a by-id read ({', '.join(by_id)}) no producer uses. "
                            "Looks like a single record would be found by listing")


def _check_views(a: Audit, realm: dict):
    for v in realm.get("views") or []:
        name = str(v.get("name") or "?")
        where = f"{v.get('_file', '')} {name}".strip()
        d = _text(v.get("description"))
        a.apply("V1", len(d) >= 40, where, "no description" if not d else f"description is {len(d)} characters; say what it answers")
        if d:
            first = _first_sentence(d)
            a.apply("V7", len(first) <= VIEW_LISTING_CUT, where,
                    f"the first sentence runs {len(first)} characters; the view listing shows {VIEW_LISTING_CUT}")
        if d:
            a.apply("V5", BOUND_WORDS.search(d) is not None, where,
                    "the description never says what the view leaves out or how it is bounded")
        params = _params(v)
        cypher = str(v.get("cypher") or v.get("goal") or "")
        for pname, spec in params.items():
            pwhere = f"{where}(${pname})"
            missing = [f for f in ("type", "description") if not spec.get(f)]
            a.apply("V2", not missing, pwhere, f"param declares no {' or '.join(missing)}")
            a.apply("V4", "default" in spec or "required" in spec, pwhere,
                    "neither a default nor a stated required: true|false, so a caller learns it is needed by being refused")
        # Partial matching owned up to: on the param itself, or on a switch param that turns it on
        # (`partial: true`), which is the better shape, because exact is then the default.
        partial = {m.group(3) for m in PARTIAL_USE.finditer(cypher)}
        switch = any(PARTIAL_WORDS.search(_text(spec.get("description"))) for spec in params.values())
        for pname in sorted(partial):
            if pname in params:
                a.apply("V3", switch,
                        f"{where}(${pname})", "matched with CONTAINS / STARTS WITH / =~, and the param's description "
                        "does not say the match is partial")
        # Honest names: a view named for one value of an enumeration it filters on several values of.
        # Only an enumeration-like property counts: `kind IN ['function','method']` under a name
        # saying Method is a definition, while `priority IN ['urgent','high']` under Urgent is a lie.
        lowered = _words(name)
        said = set()
        for prop, lst in re.findall(r"\.(\w+)\)?\s+IN\s*\[([^\]]+)\]", cypher):
            if not ENUMISH.search(prop):
                continue
            values = [x.strip().strip("'\"").lower() for x in lst.split(",") if x.strip().startswith(("'", '"'))]
            named = [x for x in values if x and re.search(rf"\b{re.escape(x)}\b", lowered)]
            if len(values) > 1 and len(named) == 1 and (prop, named[0]) not in said:
                said.add((prop, named[0]))
                a.apply("V6", False, where,
                        f"the name says {named[0]!r}, and the body keeps {prop} IN [{', '.join(values)}]")
        if not said:
            a.apply("V6", True, where)


def _first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text


def _check_realm(a: Audit, realm: dict):
    """The realm's own description reaches a model: whole in the selector that picks realms for a
    question, by its first sentence in the guide's index. Only a realm with something to query is
    picked, so only such a realm is held to it."""
    if not (realm.get("types") or realm.get("views")):
        return
    d = _text(realm.get("description"))
    a.apply("M1", len(d) >= 20, "realm.yml", "no description" if not d else f"description {d!r} says too little")
    if d:
        first = _first_sentence(d)
        a.apply("M2", len(first) <= REALM_INDEX_CUT, "realm.yml",
                f"the first sentence runs {len(first)} characters; the guide's index shows {REALM_INDEX_CUT}")


def _check_examples(a: Audit, realm: dict):
    has = any(isinstance(t.get("examples"), list) and t["examples"] for t in realm.get("types") or [])
    has = has or any(os.path.isfile(os.path.join(a.root, f)) for f in ("EXAMPLES.md", "examples.md"))
    if realm.get("types") or realm.get("views"):
        a.apply("X1", has, "realm", "no worked examples: no type declares examples:, and there is no EXAMPLES.md")


def audit_realm(root: str) -> dict:
    a = Audit(root)
    try:
        realm = read_realm(root)
    except RealmSourceError as e:
        return {"realm": os.path.basename(root.rstrip(os.sep)), "path": a.root, "error": str(e)}
    apis = api_documents(root, realm)
    _check_types(a, realm)
    _check_producers(a, realm, apis)
    _check_views(a, realm)
    _check_examples(a, realm)
    _check_realm(a, realm)
    must = sum(n for c, n in a.failed.items() if RULES[c][0] == MUST)
    should = sum(n for c, n in a.failed.items() if RULES[c][0] == SHOULD)
    return {
        "realm": realm.get("name") or os.path.basename(a.root),
        "directory": os.path.basename(a.root),
        "path": a.root,
        "source": realm.get("source"),
        "score": a.score(),
        "mustFailures": must,
        "shouldFailures": should,
        "counts": {"types": len(realm.get("types") or []), "producers": len(realm.get("producers") or []),
                   "views": len(realm.get("views") or []), "apiDocuments": len(apis)},
        "checks": {c: {"level": RULES[c][0], "checked": n, "failed": a.failed.get(c, 0)}
                   for c, n in sorted(a.checked.items())},
        "problems": realm.get("problems") or [],
        "findings": a.findings,
    }


SKIP = re.compile(r"-fix|spike|draft", re.IGNORECASE)


def is_realm(path: str) -> bool:
    return any(os.path.isfile(os.path.join(path, f)) for f in ("realm.ts", "realm.yml", "realm.yaml", "pack.yml"))


def realm_dirs(path: str) -> list:
    """[path] for a realm checkout; for a directory of checkouts, each one, minus working copies.

    A `-fix…`, spike or draft directory is somebody's work in progress beside the realm it copies,
    and so is any other directory declaring the same realm name as the `realm-<name>` checkout
    beside it: scoring a copy would score the copy, not the realm.
    """
    if is_realm(path):
        return [path]
    found = [os.path.join(path, d) for d in sorted(os.listdir(path))
             if os.path.isdir(os.path.join(path, d)) and not d.startswith(".") and not SKIP.search(d)
             and is_realm(os.path.join(path, d))]
    names = {d: _declared_name(d) for d in found}
    canonical = {n for d, n in names.items() if n and os.path.basename(d) == f"realm-{n}"}
    return [d for d in found if not (names[d] in canonical and os.path.basename(d) != f"realm-{names[d]}")]


def _declared_name(root: str):
    """realm.yml's name, cheaply; a realm.ts realm is known by its directory alone."""
    path = os.path.join(root, "realm.yml")
    if not os.path.isfile(path):
        return None
    try:
        doc = read_yaml(path)
    except Exception:  # an unreadable realm.yml is reported when the realm is audited
        return None
    return doc.get("name") if isinstance(doc, dict) else None


def render_text(results: list, verbose: bool) -> str:
    lines = []
    for r in results:
        if r.get("error"):
            lines.append(f"{r['realm']}: not audited: {r['error']}")
            continue
        c = r["counts"]
        lines.append(f"{r['directory']}  score {'—' if r['score'] is None else r['score']}  ·  {r['mustFailures']} MUST, {r['shouldFailures']} SHOULD findings"
                     f"  ·  {c['types']} types, {c['producers']} producers, {c['views']} views")
        for problem in r["problems"]:
            lines.append(f"  PROBLEM  {problem}")
        if verbose or len(results) == 1:
            for f in sorted(r["findings"], key=lambda f: (f["level"] != MUST, f["id"], f["where"])):
                lines.append(f"  {f['level']:<6} {f['id']}  {f['where']}: {f['message']}")
        lines.append("")
    if len(results) > 1:
        scored = [r for r in results if not r.get("error") and r["score"] is not None]
        if scored:
            mean = round(sum(r["score"] for r in scored) / len(scored))
            lines.append(f"{len(scored)} realms audited, mean score {mean}; "
                         f"{sum(r['mustFailures'] for r in scored)} MUST findings in all.")
    return "\n".join(lines)


def render_markdown(results: list) -> str:
    scored = [r for r in results if not r.get("error")]
    ids = sorted(RULES)
    out = ["| Realm | Score | MUST | SHOULD | Types | Producers | Views | " + " | ".join(ids) + " |",
           "|---|---:|---:|---:|---:|---:|---:|" + "---:|" * len(ids)]
    for r in sorted(scored, key=lambda r: (-(r["score"] if r["score"] is not None else -1), r["directory"])):
        cells = []
        for cid in ids:
            ch = r["checks"].get(cid)
            cells.append("" if not ch else (f"{ch['failed']}/{ch['checked']}" if ch["failed"] else "✓"))
        c = r["counts"]
        out.append(f"| {r['directory']} | {'—' if r['score'] is None else r['score']} | {r['mustFailures']} | {r['shouldFailures']} | "
                   f"{c['types']} | {c['producers']} | {c['views']} | " + " | ".join(cells) + " |")
    for r in results:
        if r.get("error"):
            out.append(f"| {r['realm']} | — | | | | | | not audited: {r['error']} |")
    if len(scored) > 1:
        # Across every realm, by rule: where the estate as a whole stands, and so what to fix first.
        out += ["", "| Rule | Level | Realms | Checked | Failed | Met |", "|---|---|---:|---:|---:|---:|"]
        for cid in ids:
            rows = [r["checks"][cid] for r in scored if cid in r["checks"]]
            if not rows:
                continue
            checked = sum(x["checked"] for x in rows)
            failed = sum(x["failed"] for x in rows)
            out.append(f"| {cid} {RULES[cid][1]} | {RULES[cid][0]} | {len(rows)} | {checked} | {failed} | "
                       f"{math.floor(100 * (1 - failed / checked))}% |")
    return "\n".join(out)


def run_audit(path: str, *, as_json: bool = False, markdown: bool = False, verbose: bool = False,
              fail_under: int | None = None) -> int:
    if not os.path.isdir(path):
        print(f"  Not a directory: {path}")
        return 2
    dirs = realm_dirs(path)
    if not dirs:
        print(f"  {path} is not a realm checkout (no realm.yml or realm.ts), and holds none.")
        return 2
    results = [audit_realm(d) for d in dirs]
    if as_json:
        print(json.dumps(results[0] if is_realm(path) else results, indent=2))
    elif markdown:
        print(render_markdown(results))
    else:
        print(render_text(results, verbose))
    if any(r.get("error") for r in results):
        return 2
    if fail_under is not None and any(r["score"] is not None and r["score"] < fail_under for r in results):
        return 1
    return 0
