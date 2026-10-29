#!/usr/bin/env python3
"""A bundle never carries a secret, and a bundle from elsewhere is read before it is trusted.

WHY THIS EXISTS. `embabel export` writes a file people send to each other and to support. The one thing it must
never do is carry a credential, and the list of files the server writes credentials into will grow without anyone
remembering this one. So the content scan — the layer that catches a secret file nobody listed — is driven here with
one sample of every shape it knows, through the same grep the export runs in the server image, and the parts of the
export that choose files are held to the list of credential paths.

Nothing here talks to Docker: the helper container is a patch, and the grep runs on this machine (GNU grep, as in
the server image; on another grep that half is skipped and says so).

    python3 scripts/check-bundle.py
"""
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from embabel_setup import bundle, bundleimport, bundlesecrets, setupbundle  # noqa: E402

failures = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


# One file per shape, each holding a made-up value of that shape — and one that holds none.
SAMPLES = {
    "hash.yml": b"admin: {bcrypt}$2a$10$abcdefghijklmnopqrstuuvwxyzabcdefghijklmnopqrstuvwxyz01\n",
    "apikey.yml": b"key: emb_abcdefghijklmnopqrstuvwxyz\n",
    "openai.txt": b"OPENAI=sk-proj-abcdefghijklmnopqrstuvwxyz\n",
    "anthropic.txt": b"key sk-ant-abcdefghijklmnopqrstuvwxyz\n",
    "google.json": b'{"k": "AIzaabcdefghijklmnopqrstuvwxyz012345678"}\n',
    "github.yml": b"token: ghp_abcdefghijklmnopqrstuvwxyz\n",
    "slack.yml": b"bot: xoxb-1234567890-abcdef\n",
    "aws.ini": b"aws_access_key_id = AKIAABCDEFGHIJKLMNOP\n",
    "pkey.txt": b"-----BEGIN RSA PRIVATE KEY-----\nMII\n",
    "oauth.json": b'{"refresh_token": "abcdefghijklmnopqrstuvwxyz0123"}\n',
    "client.yml": b"client_secret: abcdefghijklmnop\n",
    "clone.yml": b"repo: https://x-access-token:abcdefghijklmnop@github.com/acme/realm.git\n",
    "jdbc.yml": b"jdbcUrl: jdbc:postgresql://db:5432/app?user=app&password=hunter22\n",
}
CLEAN = b"repo: https://github.com/acme/realm.git\njdbcUrl: jdbc:postgresql://db:5432/app\npasswordEnv: DB_PASSWORD\n"


def check_shapes_in_python() -> None:
    for name, data in SAMPLES.items():
        check(bundlesecrets.shapes_in(data), f"the scan does not see the credential in {name}")
    check(not bundlesecrets.shapes_in(CLEAN), f"the scan sees a credential in a clean file: {bundlesecrets.shapes_in(CLEAN)}")


def check_shapes_in_grep() -> None:
    """The scan as the export runs it: a shell script of grep -E, which must find the same files and print no value."""
    version = subprocess.run(["grep", "--version"], capture_output=True, text=True).stdout
    if "GNU" not in version:
        print("  - the grep half needs GNU grep (the server image's); skipped on this machine")
        return
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in {**SAMPLES, "clean.yml": CLEAN}.items():
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(data)
        listing = os.path.join(tmp, "..list")
        with open(listing, "w") as f:
            f.write("\n".join([*SAMPLES, "clean.yml"]) + "\n")
        out = subprocess.run(["sh", "-c", bundlesecrets.scan_script(tmp, listing)], capture_output=True, text=True).stdout
    found = {line.split("\t", 1)[1] for line in out.splitlines() if "\t" in line}
    for name in SAMPLES:
        check(name in found, f"the export's grep does not see the credential in {name}")
    check("clean.yml" not in found, "the export's grep flags a clean file")
    for data in SAMPLES.values():
        value = data.split(b":", 1)[-1].strip()[-12:].decode(errors="replace")
        check(value not in out, "the scan printed part of a credential's value")


def check_selection() -> None:
    """What an export selects never reaches a path the server keeps credentials in."""
    roots = ("users", "chat-attachments", *bundle.SHARED_SETUP)
    for secret in bundlesecrets.SECRET_PATHS:
        check(not any(secret == r or secret.startswith(r + "/") for r in roots), f"an export root contains {secret}")
    for name in ("secrets.env", ".env", "providers.env", ".credentials.yml", ".setup-token", "id_rsa", "server.key"):
        import fnmatch
        check(any(fnmatch.fnmatch(name, pattern) for pattern in bundlesecrets.SECRET_NAMES),
              f"a file named {name} would be selected")


def check_refusal() -> None:
    """A selected file with a credential stops the export, naming the file and the kind, never the value."""
    fake = subprocess.CompletedProcess([], 0, stdout=b"a GitHub token\tusers/ann/default/config/mcp/github.yml\n", stderr=b"")
    with patch.object(bundle, "_helper", return_value=fake):
        try:
            bundle._refuse_secrets("image", "/list")
            check(False, "a scan that found a credential did not stop the export")
        except bundle.BundleError as e:
            check("config/mcp/github.yml" in str(e) and "GitHub token" in str(e), f"the refusal does not name the file: {e}")


def check_setup_withholds() -> None:
    lens = {"id": "keys", "script": "fetch('https://api.example.com', {headers: {Authorization: 'Bearer ghp_abcdefghijklmnopqrstuvwxyz'}})"}
    withheld = []
    kept = setupbundle._without_secrets([lens, {"id": "plain", "script": "gateway.kg.query('MATCH (n) RETURN n')"}], "lens", "id", withheld)
    check([k["id"] for k in kept] == ["plain"], "a lens holding a token was kept in a setup bundle")
    check(withheld and "ghp_" not in withheld[0], "the withheld note is missing, or shows the value")
    check(setupbundle._without_userinfo("https://x-access-token:abc@github.com/acme/r.git") == "https://github.com/acme/r.git",
          "a realm's clone URL keeps the token it was cloned with")


def check_manifest_gate() -> None:
    """A file that is not a bundle, or one from a newer format, is refused before anything changes."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "b.tar.gz")
        payload = json.dumps({"format": bundle.FORMAT + 1}).encode()
        with open(path, "wb") as f, bundle.compressor(f) as raw, tarfile.open(fileobj=raw, mode="w|") as tar:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        manifest = bundleimport.read_manifest(path)
        try:
            bundleimport.check_compatible(manifest)
            check(False, "a bundle of a newer format was accepted")
        except bundle.BundleError:
            pass
        junk = os.path.join(tmp, "junk.tar.gz")
        with open(junk, "wb") as f:
            f.write(b"not a bundle")
        try:
            bundleimport.read_manifest(junk)
            check(False, "a file that is not a bundle was read as one")
        except bundle.BundleError:
            pass


for step in (check_shapes_in_python, check_shapes_in_grep, check_selection, check_refusal, check_setup_withholds, check_manifest_gate):
    step()

if failures:
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
print("  ✓ every credential shape is caught by the export's scan, and none is printed")
print("  ✓ an export never selects a credential path, and a setup withholds what holds a key")
print("  ✓ a bundle is read and its format checked before anything changes")
