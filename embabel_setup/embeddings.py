"""The embedding model: what this appliance uses to index documents, if anything.

AN APPLIANCE SHIPS WITHOUT ONE. The local model is about 1.1GB and needs Docker Model
Runner, which is a Docker Desktop feature — so requiring it made every first run
download it before anybody had uploaded a document, and ruled out plain Docker Engine
entirely, where `docker model` is not a command. Somebody evaluating the product should
not pay for a capability they have not asked for.

So document features are OFF rather than broken, and this is how they come on:

    embabel embeddings use local     the local model, on this machine, nothing leaves it
    embabel embeddings use openai    the provider key already given, nothing to download
    embabel embeddings off           back to no embedding model

STICKY, and that is the whole reason this is a deliberate act rather than a default.
Vectors already in the graph were made by whichever model made them, and a vector index
is built at that model's dimensions — so changing the model means re-embedding
everything. The server does that properly (drop indexes, re-embed each store, rebuild at
the new width, roll back on failure); this says how much work it is about to ask for
before it asks.
"""
import base64
import json
import os
import urllib.error
import urllib.request

from .colour import MIDDOT, TICK, bold, dim, warn
from .core import SetupError, prompt
from .dockerlib import LOCAL_EMBEDDING_MODEL, _docker
from .settings import env_file_value, set_env_var

MODEL_VAR = "ASSISTANT_EMBEDDING_MODEL"

# What the shorthands mean. `openai` is the provider default rather than a pinned name
# so it follows whatever the server considers current.
CHOICES = {
    "local": LOCAL_EMBEDDING_MODEL,
    "openai": "text-embedding-3-small",
}


def configured_embedding_model() -> str | None:
    return os.environ.get(MODEL_VAR) or env_file_value(MODEL_VAR) or None


def resolve_choice(choice: str) -> str:
    """A shorthand, or a model name passed through untouched."""
    return CHOICES.get(choice, choice)


def embeddings_status(base: str, auth: str) -> dict:
    """What the appliance says about its own embedding model."""
    request = urllib.request.Request(f"{base}/api/v1/embeddings")
    request.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise SetupError("The appliance did not accept that password.")
        raise SetupError(f"The appliance answered {e.code}.")
    except urllib.error.URLError as e:
        raise SetupError(f"Could not reach the appliance at {base} ({e.reason}).")


def describe_embeddings(status: dict, chosen: str | None) -> None:
    if status.get("configured"):
        print(f"  {TICK} Embedding model: {bold(status.get('model') or '?')}"
              + dim(f"   {status.get('dimensions')} dimensions"))
        print("  " + dim("Document upload, search and ask are available."))
    else:
        print(f"  {MIDDOT} " + bold("No embedding model."))
        print("  " + dim("Document features are off until one is set:"))
        print("     embabel embeddings use local    " + dim("~1.1GB, runs here, nothing leaves"))
        print("     embabel embeddings use openai   " + dim("uses the provider key you gave"))
    if chosen and not status.get("configured"):
        # The two disagree, which means a restart is pending — worth saying, because
        # otherwise the command looks as though it did nothing.
        print()
        print("  " + warn(f"{MODEL_VAR}={chosen} is set but not in effect."))
        print("  " + dim("Restart to apply: embabel down && embabel up"))


def pull_local_model() -> None:
    """Fetch the local embedding model, so the first document does not wait on it."""
    print(f"  Pulling {bold(LOCAL_EMBEDDING_MODEL)} " + dim("(about 1.1GB, once)"))
    run = _docker("model", "pull", LOCAL_EMBEDDING_MODEL, timeout=3600)
    if not run or run.returncode != 0:
        raise SetupError(
            "Could not pull the model. Docker Model Runner is a Docker Desktop feature:\n"
            "  enable it in Settings → AI, or `docker desktop enable model-runner`.\n"
            "  On plain Docker Engine, install the docker-model-plugin package —\n"
            "  or use `embabel embeddings use openai`, which downloads nothing."
        )


def choose_embeddings(choice: str) -> str:
    """Record the choice, pulling the local model first when that is what was asked for."""
    model = resolve_choice(choice)
    if choice == "local" or model.startswith(("ai/", "docker.io/ai/")):
        pull_local_model()
    set_env_var(MODEL_VAR, model, why=(
        "# The embedding model. Written by `embabel embeddings use`; document features",
        "# are off while this is unset. Changing it re-embeds everything already indexed.",
    ))
    return model


def clear_embeddings() -> None:
    if not configured_embedding_model():
        print(f"  {MIDDOT} " + dim("No embedding model set."))
        return
    # Emptied rather than deleted, for the same reason the sandbox setting is: somebody
    # reading .env should see that the choice was made and undone.
    set_env_var(MODEL_VAR, "")
    print(f"  {TICK} Embedding model cleared — document features will be off.")
    print("  " + warn("Restart to apply: embabel down && embabel up"))


def offer_embeddings() -> str | None:
    """The first-run offer. Declining is the default, and costs nothing.

    Asked rather than assumed, and NOT defaulted to yes: the model is a gigabyte and
    needs a Docker Desktop feature, and somebody installing for the first time has not
    yet decided whether they care about documents. The offer exists so they know the
    capability is there — an appliance that silently cannot index anything teaches
    nobody — and the default is no so that knowing costs nothing.
    """
    answer = prompt("\n  Set up document search now? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        print(f"  {MIDDOT} " + dim("Skipped. Turn it on any time: embabel embeddings use local"))
        return None
    print("\n     1) local    " + dim("~1.1GB, runs on this machine, nothing leaves it"))
    print("     2) openai   " + dim("uses the provider key you gave, nothing to download"))
    pick = prompt("  Choose 1-2 [1]: ").strip() or "1"
    return choose_embeddings("openai" if pick == "2" else "local")
