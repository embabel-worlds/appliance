"""The appliance's own documentation, into the world it just created.

A new world is empty, so the first question anybody has — what is this, and what
can I ask it — was one the product could not answer about itself. Embeddings are
local, so this costs nothing and sends nothing anywhere.
"""
import base64
import os
import secrets
import shutil
import tempfile
import urllib.request

from .colour import MIDDOT, TICK, dim
from .core import APPLIANCE_DIR
from .status import STATUS

# ── seeding the documentation ───────────────────────────────────────────────
#
# A NEW WORLD IS EMPTY, and the first question anybody has — "what is this, and
# what can I ask it?" — is one the product could not answer about itself. The
# config views answer the structural half (what realms, what views, which model
# runs which job). This answers the prose half: the appliance's own guides, in
# the graph, searchable, on the first run.
#
# It costs nothing to send anywhere. Embeddings are local and always have been,
# so this is ~135KB through a model already running on this machine — seconds,
# no tokens, no provider key. It is the one corpus every new user wants and the
# only one we can ship without asking a question first.
#
# THE CREDENTIAL IS THE MINTED BEARER TOKEN, not the password the user just
# typed. The MCP step mints one for precisely this kind of call and hands it
# over once; setup holding a password to make an HTTP request would be a design
# smell that outlives the reason for it.
#
# BEST EFFORT, ALWAYS. Setup has succeeded by the time this runs. A world with
# no documentation in it is a smaller problem than an installer that fails at
# the finish line, so every failure here is reported and swallowed.

# The operator account, as it is created. Process memory only: never written,
# never logged, and used solely to authenticate the documentation upload below.
_ACCOUNT: tuple[str, str] | None = None
# WHAT GOES IN, AND WHY TWO THINGS CAME OUT.
#
# DISCOVERY.md opened with "Status: proposal. Nothing on this page is built."
# and was seeded anyway. Asked "what is virtual cypher", the world answered from
# it — a filesystem producer with three tiers, none of which exists — because it
# was one of only two seeded files mentioning the term and the other said little.
# A retrieval corpus has no way to discount a document; whatever matches wins. A
# proposal in a knowledge base is not incomplete information, it is confident
# misinformation, and it belongs nowhere near one.
#
# AGENT_GUIDE.md came out for a smaller reason: it is instructions to a coding
# agent about how to drive an appliance, not documentation of what one is.
SEED_DOCS = ("README.md", "CLI.md", "PHONE_HOME.md", "WORLD_TEMPLATES.md")
# The skills ARE the user-facing documentation of authoring — views, handlers,
# apps, realms — and world-authoring is the only thing this repo ships that
# explains virtual Cypher at all.
SEED_DOC_DIRS = ("docs/guide", "skills")
# THE SPEC IS NOT IN THIS REPO, and it is the document that actually defines
# virtual Cypher, realms, types and composition. It lives in realm-spec, which
# worlds.embabel.com vendors the same way. Fetched over raw.githubusercontent —
# not the API, so no rate limit and no token — and skipped in silence if the
# network is not there, because a seeded world is a nicety and an installer that
# fails without internet is not.
SPEC_REPO = "https://raw.githubusercontent.com/embabel-worlds/realm-spec/main"
SPEC_DOCS = ("VIRTUAL_CYPHER.md", "VIRTUAL_CYPHER_GUIDE.md", "README.md",
             "DECLARING_TYPES.md", "LABELS_AND_COMPOSITION.md",
             "EXTERNAL_DOCUMENTS.md", "CONTEXT.md")
# Big enough for the guides, small enough that a stray file cannot become an
# ingestion job somebody did not ask for.
SEED_MAX_BYTES = 512 * 1024
def remember_account(username: str, password: str) -> None:
    """Hold the account setup just created, for the upload below and nothing else.

    A SETTER, not a `global` from another module. The wizard used to reach in
    with `global _ACCOUNT` while living in the same file; after the split that
    statement bound a name in setup.py's namespace instead, seed.py's stayed
    None, and the seeding reported "no credential" on every install — silently,
    because both halves were individually correct.
    """
    global _ACCOUNT
    _ACCOUNT = (username, password)


def seed_credential(api_token: str | None) -> str | None:
    """An Authorization header for the seed upload, or None if there is nothing.

    THE ACCOUNT, NOT THE MCP TOKEN — and the reverse of what this function did
    first. The token the MCP step mints authorizes `/mcp`; the REST surface does
    not accept it, and answers 401 to `POST /api/v1/documents/upload`. Measured
    against a live appliance: bearer 401, Basic 200 `{"status":"ingested"}`.
    Preferring the token therefore guaranteed the seed failed on exactly the
    installs where the MCP step HAD run, which is most of them.

    `api_token` is still taken when there is no account — a run driven with
    --token against an already-provisioned appliance has one and not the other.
    """
    if _ACCOUNT:
        return "Basic " + base64.b64encode(f"{_ACCOUNT[0]}:{_ACCOUNT[1]}".encode()).decode()
    if api_token:
        return f"Bearer {api_token}"
    return None
def documentation_files() -> list[str]:
    """The guides, as absolute paths. Named explicitly rather than globbed from the
    repo root: CONTRIBUTING and CLAUDE.md are instructions to people working ON
    the appliance, and a user searching their world should not get them back."""
    found = []
    for name in SEED_DOCS:
        path = os.path.join(APPLIANCE_DIR, name)
        if os.path.exists(path) and os.path.getsize(path) <= SEED_MAX_BYTES:
            found.append(path)
    for folder in SEED_DOC_DIRS:
        directory = os.path.join(APPLIANCE_DIR, folder)
        if not os.path.isdir(directory):
            continue
        # Recursive: skills/ is skills/<name>/SKILL.md, a level deeper than docs/.
        for root, _dirs, files in os.walk(directory):
            for entry in sorted(files):
                path = os.path.join(root, entry)
                if entry.endswith(".md") and os.path.getsize(path) <= SEED_MAX_BYTES:
                    found.append(path)
    return sorted(found)
def fetch_spec_documents(into: str) -> list[str]:
    """The realm spec, downloaded to a temporary directory. Best effort."""
    fetched = []
    for name in SPEC_DOCS:
        try:
            with urllib.request.urlopen(f"{SPEC_REPO}/{name}", timeout=30) as response:
                body = response.read()
        except Exception:
            continue
        if not body or len(body) > SEED_MAX_BYTES:
            continue
        # Prefixed so a spec page cannot be mistaken for an appliance page in a
        # search result — "realm-spec VIRTUAL_CYPHER.md" says where it came from.
        path = os.path.join(into, f"realm-spec {name}")
        with open(path, "wb") as f:
            f.write(body)
        fetched.append(path)
    return fetched
def _upload_document(base: str, auth: str, path: str) -> bool:
    """One multipart POST, by hand — stdlib only, like everything else here.

    `auth` is a complete Authorization header value, Bearer or Basic, because
    which credential is available depends on which steps the wizard ran.
    """
    boundary = "----embabel" + secrets.token_hex(8)
    with open(path, "rb") as f:
        content = f.read()
    name = os.path.basename(path)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

    request = urllib.request.Request(
        f"{base}/api/v1/documents/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": auth},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return 200 <= response.status < 300
    except Exception:
        return False
def seed_documentation(base: str, auth: str) -> None:
    """Put the appliance's own guides into the world. Never raises."""
    files = documentation_files()
    workspace = tempfile.mkdtemp(prefix="embabel-spec-")
    files += fetch_spec_documents(workspace)
    if not files:
        return
    STATUS.start(f"Indexing the documentation   {dim('0 of ' + str(len(files)))}")
    done = 0
    try:
        for index, path in enumerate(files, 1):
            STATUS.set(f"Indexing the documentation   {dim(f'{index} of {len(files)}')}  "
                       + dim(os.path.basename(path)))
            if _upload_document(base, auth, path):
                done += 1
    except Exception:
        pass  # best effort; setup has already succeeded
    shutil.rmtree(workspace, ignore_errors=True)
    if done:
        STATUS.stop(f"  {TICK} Indexed {done} guide(s) — ask the world about itself, "
                    + dim("no key needed."))
    else:
        # Silent failure would be worse than none: somebody would search for a
        # thing the product implied was there.
        STATUS.stop(f"  {MIDDOT} " + dim("Could not index the documentation — "
                                         "the appliance is fine; add it later from Documents."))
