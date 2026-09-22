#!/usr/bin/env python3
"""Regression checks for what `embabel embeddings use` writes.

The appliance image ships no provider starters, so a HOSTED MODEL NAME matches nothing
registered on the server and resolves to the setup-required placeholder. `use openai` used
to write exactly such a name; it worked only because the server carried an appliance-only
service in front of its default, and that service is gone now that an embedding default can
name a role resolved per call (embabel/embabel-agent#2041, embabel/me#1487).

So the thing worth checking is narrow and load-carrying: the hosted choice must write the
ROLE, and nothing that somebody might plausibly type may write a hosted model name instead.
The failure it guards against is silent — documents simply never index, and nothing says why.
"""
import json
import os
from unittest.mock import patch
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embabel_setup.embeddings import (  # noqa: E402
    CHOICES,
    HOSTED_ROLE,
    LEGACY_HOSTED_MODELS,
    resolve_choice,
)
from embabel_setup.dockerlib import LOCAL_EMBEDDING_MODEL  # noqa: E402

# The hosted choice writes a role, under every spelling a person might reach for.
for spelling in ("hosted", "openai"):
    assert resolve_choice(spelling) == HOSTED_ROLE, \
        f"`use {spelling}` must write the role, not a model name — it wrote {resolve_choice(spelling)!r}"

# A hosted model name typed from memory or an older README resolves to the role too, rather
# than being passed through to fail later as documents that will not index.
for model in LEGACY_HOSTED_MODELS:
    assert resolve_choice(model) == HOSTED_ROLE, \
        f"the hosted model name {model!r} must map to the role, not pass through"

# `local` stays a NAME, deliberately: one local model, registered at startup, needing no key.
assert resolve_choice("local") == LOCAL_EMBEDDING_MODEL, "`use local` must still pin the local model"
assert LOCAL_EMBEDDING_MODEL.startswith("docker.io/ai/"), \
    "the local model must stay the registry-qualified id the Model Runner reports"

# Pass-through stays for anything else: no list here could keep up with local model ids.
for passthrough in ("docker.io/ai/some-other-embedder:1.0", "a-model-nobody-here-knows"):
    assert resolve_choice(passthrough) == passthrough, \
        f"{passthrough!r} must pass through untouched"

# The role is written verbatim into .env and read by the server as
# embabel.models.default-embedding-model, so it has to match a key under
# `embabel.models.embedding-roles` there. A rename on either side breaks embedding silently.
assert HOSTED_ROLE == "hosted", \
    "the role name is a cross-repo contract with me's application.yml — change both together"

# Choosing hosted must not try to pull anything: that path is the local model's alone.
assert not CHOICES[HOSTED_ROLE].startswith(("ai/", "docker.io/ai/")), \
    "the hosted choice must not look like a Model Runner id, or choose_embeddings would pull it"

# --- the repair for appliances upgraded from a build that wrote a model name ------------
import tempfile  # noqa: E402
from unittest.mock import patch  # noqa: E402

# --- applying to a RUNNING appliance --------------------------------------------------------
# The .env write survives a restart; the API call saves waiting for one. Both, not either — and
# every failure path must leave the restart route intact, because the CLI ships independently of
# the server and an older one will refuse a role by name.

from embabel_setup import embeddings as e  # noqa: E402


def migrate_with(env_value):
    """Run the repair against a throwaway .env holding env_value, and report what it left."""
    with tempfile.TemporaryDirectory() as tmp:
        env = os.path.join(tmp, ".env")
        with open(env, "w") as f:
            if env_value is not None:
                f.write(f"{e.MODEL_VAR}={env_value}\n")
        with patch.object(e, "env_file_value", lambda var: env_value), \
             patch.dict(os.environ, {}, clear=True):
            written = {}
            with patch.object(e, "set_env_var", lambda var, val, why=None: written.update({var: val})):
                returned = e.migrate_legacy_embedding_choice()
            return returned, written.get(e.MODEL_VAR)


# A name left by an older `use openai` is repaired to the role. Without this, an upgraded
# appliance keeps a name newer builds cannot resolve and documents silently stop indexing.
for legacy in LEGACY_HOSTED_MODELS:
    returned, written = migrate_with(legacy)
    assert returned == HOSTED_ROLE, f"{legacy!r} should migrate to the role, got {returned!r}"
    assert written == HOSTED_ROLE, f"{legacy!r} should be rewritten in .env, wrote {written!r}"

# Everything else is left exactly alone, and says it changed nothing. Idempotent on a value
# already migrated, and never touches a local model id or an appliance that chose nothing.
for untouched in (HOSTED_ROLE, LOCAL_EMBEDDING_MODEL, "", None, "a-model-nobody-here-knows"):
    returned, written = migrate_with(untouched)
    assert returned is None, f"{untouched!r} must not be migrated, got {returned!r}"
    assert written is None, f"{untouched!r} must not be rewritten, wrote {written!r}"

# --- the copy a user READS must teach the verb we want them to type -----------------------
# `openai` still works as an alias, so nothing breaks when copy says it — which is exactly why
# this needs checking rather than being noticed. The installer's own "document search, when you
# want it" text kept recommending the old verb after the rename, and that text is the first and
# often only place anyone meets the command.
import glob  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in glob.glob(os.path.join(REPO, "copy", "*.txt")) + [os.path.join(REPO, "CLI.md")]:
    with open(path) as f:
        body = f.read()
    assert "embeddings use openai" not in body, (
        f"{os.path.relpath(path, REPO)} still tells people `embabel embeddings use openai`. "
        f"The hosted choice is `{HOSTED_ROLE}`; the old spelling works but must not be taught."
    )

class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def apply_with(payload=None, raises=None):
    def fake_urlopen(request, timeout=0):
        if raises is not None:
            raise raises
        return _FakeResponse(payload)

    with patch.object(e.urllib.request, "urlopen", fake_urlopen):
        return e.apply_now("hosted", "http://appliance", "Basic xyz")


# The server reports what it ENDED UP with, and that is what counts as applied. A 200 alone would
# call a no-op a success — the same mistake the server itself used to make.
assert apply_with({"status": {"model": "hosted"}}) is True
assert apply_with({"result": {"newModel": "hosted"}}) is True
assert apply_with({"status": {"model": "text-embedding-3-small"}}) is False, \
    "a server that ended up on a different model has NOT applied the choice"
assert apply_with({}) is False

# Every failure is soft: .env is already right, so the caller falls back to the restart it was
# going to print anyway. An older server refusing a role by name is the case that matters.
import urllib.error  # noqa: E402
assert apply_with(raises=urllib.error.URLError("down")) is False
assert apply_with(raises=OSError("connection refused")) is False

print("embeddings role: ok")
