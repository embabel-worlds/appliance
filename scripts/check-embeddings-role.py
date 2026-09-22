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
import os
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

print("embeddings role: ok")
