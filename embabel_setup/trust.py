"""Certificate authorities this appliance trusts beyond the public ones.

A company that inspects its outbound traffic, or runs its own model gateway,
signs with an authority no public list has heard of. The machine trusts it —
somebody installed it in the system keychain — and the container does not, so
`curl` works from the terminal and the same address fails from the appliance
with nothing to tell the two apart.

Trust is a FOLDER, not a setting. `certs/` in this checkout is mounted read-only
into the container, and the image imports whatever is there when it starts: into
the Java trust store, which every model call goes through, and into the system
one, which curl and git read. Nothing is recorded anywhere else, so what is
trusted is exactly what is in the folder, and an upgrade — which only ever
writes files the download contains — leaves it alone.

ONLY AUTHORITIES ARE KEPT. A server's own certificate says who it is, not who
vouches for it, and trusting one stops working the day it is renewed. So a file
is read for the authorities in it and anything else is left out, which also
means the whole chain as a browser exports it can be handed over as it is.

Certificates are parsed by Python's own ssl module rather than by hand: the
question "is this an authority" has an answer in the certificate, and OpenSSL is
the thing that knows how to read it.
"""

from __future__ import annotations
import hashlib
import os
import re
import ssl
import time
from typing import NamedTuple

from .colour import MIDDOT, TICK, bold, dim, warn
from .core import APPLIANCE_DIR, MODE_SERVICE, SetupError
from .dockerlib import _compose, _docker, find_mode_container

CERTS_DIR = os.path.join(APPLIANCE_DIR, "certs")

# The image's importer reads /certificates/*crt and nothing else, so this suffix
# is the contract and not a preference.
SUFFIX = ".crt"

PEM_BEGIN = b"-----BEGIN CERTIFICATE-----"
_PEM_BLOCK = re.compile(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL)

# How long the importer is given to finish after the container is recreated. It
# runs before Java starts, so this is seconds of keytool and not the app's boot.
IMPORT_WAIT_SECONDS = 60


class Authority(NamedTuple):
    """One certificate authority, as a person can check it against what they were given."""
    name: str
    fingerprint: str
    expires: str
    pem: str


def _named(parts) -> dict[str, str]:
    """ssl describes a name as a tuple of one-pair tuples; this is the same thing as a dict."""
    return {key: value for part in parts for key, value in part}


def _authority(der: bytes) -> Authority | None:
    """The authority in one DER certificate, or None if it is not one.

    Asked one certificate at a time so the description and the bytes cannot be
    paired up wrongly: get_ca_certs() lists authorities only, and makes no promise
    about order across two calls.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        context.load_verify_locations(cadata=der)
    except (ssl.SSLError, ValueError):
        return None
    described = context.get_ca_certs()
    if not described:
        return None
    subject = _named(described[0].get("subject", ()))
    digest = hashlib.sha256(der).hexdigest().upper()
    return Authority(
        name=subject.get("commonName") or subject.get("organizationName") or "(unnamed)",
        fingerprint=":".join(digest[i:i + 2] for i in range(0, len(digest), 2)),
        expires=described[0].get("notAfter", ""),
        pem=ssl.DER_cert_to_PEM_cert(der),
    )


def _is_certificate(der: bytes) -> bool:
    """Whether these bytes parse as a certificate of any kind, authority or not."""
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=der)
        return True
    except (ssl.SSLError, ValueError):
        return False


def read_authorities(raw: bytes) -> tuple[list[Authority], int]:
    """The authorities in a file, and how many certificates in it were not one.

    PEM or DER, one certificate or a chain. Raises when something in it is not a
    certificate at all, because that is a wrong file rather than an empty answer.
    """
    try:
        ders = ([ssl.PEM_cert_to_DER_cert(block.decode("ascii")) for block in _PEM_BLOCK.findall(raw)]
                if PEM_BEGIN in raw else [raw])
    except (ValueError, UnicodeDecodeError):
        ders = []
    if not ders or not all(_is_certificate(der) for der in ders):
        raise SetupError("That file is not a certificate. It wants PEM (-----BEGIN CERTIFICATE-----) or DER.")
    found = [_authority(der) for der in ders]
    authorities = [a for a in found if a]
    return authorities, len(found) - len(authorities)


def _stored_name(authority: Authority) -> str:
    """The file an authority is kept in: its own name, so the folder reads as a list.

    The fingerprint's first bytes follow when the name is taken by a DIFFERENT
    certificate — a renewed root has its predecessor's name, and both are wanted
    while one replaces the other.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", authority.name.lower()).strip("-") or "authority"
    path = os.path.join(CERTS_DIR, slug + SUFFIX)
    if not os.path.exists(path):
        return slug + SUFFIX
    with open(path, "rb") as f:
        same = authority.pem.encode("ascii") == f.read()
    return slug + SUFFIX if same else f"{slug}-{authority.fingerprint.replace(':', '')[:8].lower()}{SUFFIX}"


def add_trusted(path: str) -> tuple[list[tuple[str, Authority]], int]:
    """Copy the authorities in [path] into certs/, ONE FILE EACH. Returns what was
    stored under which name, and how many certificates were left out for not being
    an authority.

    One each because the system trust store indexes a file by the certificate in it
    and skips a file holding several — so a chain kept as one file would be trusted
    by Java and only partly by everything else in the container.
    """
    source = os.path.expanduser(path)
    if not os.path.isfile(source):
        raise SetupError(f"{source} is not a file.")
    with open(source, "rb") as f:
        authorities, skipped = read_authorities(f.read())
    if not authorities:
        raise SetupError(
            "That is a server's own certificate, not the authority that signed it. "
            "Trust is given to the authority: the LAST certificate in the chain, "
            "which your IT team or your system keychain can export."
        )
    os.makedirs(CERTS_DIR, exist_ok=True)
    stored = []
    for authority in authorities:
        name = _stored_name(authority)
        with open(os.path.join(CERTS_DIR, name), "w", encoding="ascii") as f:
            f.write(authority.pem)
        stored.append((name, authority))
    return stored, skipped


def trusted() -> list[tuple[str, list[Authority]]]:
    """Every file in certs/ the importer will read, with the authorities in it."""
    if not os.path.isdir(CERTS_DIR):
        return []
    listed = []
    for name in sorted(os.listdir(CERTS_DIR)):
        if not name.endswith(SUFFIX):
            continue
        with open(os.path.join(CERTS_DIR, name), "rb") as f:
            raw = f.read()
        try:
            listed.append((name, read_authorities(raw)[0]))
        except SetupError:
            # Somebody's own file, put here by hand. Listed, so it is not a
            # mystery, and left alone, because it is theirs.
            listed.append((name, []))
    return listed


def remove_trusted(name: str) -> bool:
    """Take one file out of certs/. True when there was one."""
    target = name if name.endswith(SUFFIX) else name + SUFFIX
    path = os.path.join(CERTS_DIR, os.path.basename(target))
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def describe(authorities: list[Authority], indent: str = "    ", show_name: bool = True) -> None:
    for a in authorities:
        if show_name:
            print(f"{indent}{bold(a.name)}")
        print(f"{indent}  {dim('SHA-256  ' + a.fingerprint)}")
        if a.expires:
            print(f"{indent}  {dim('expires  ' + a.expires)}")


def _imported(container: str, fingerprints: set[str]) -> set[str]:
    """Which of [fingerprints] the container's Java trust store holds right now."""
    run = _docker("exec", container, "sh", "-c",
                  'keytool -list -keystore "$JAVA_HOME/lib/security/cacerts" -storepass changeit',
                  timeout=60)
    if run is None or run.returncode != 0:
        return set()
    listing = run.stdout.upper()
    return {f for f in fingerprints if f in listing}


def apply_trust(mode: str | None, expect: list[Authority]) -> bool:
    """Recreate the app so it starts with what certs/ now holds, and check it did.

    RECREATED, NOT RESTARTED. The importer writes into the container's own trust
    store, which a restart keeps — so a removed authority would stay trusted until
    the next upgrade happened to replace the container. A new container starts
    from the image's store and imports only what the folder holds.

    The check is the point. An image built before the importer was wired in mounts
    the folder and ignores it, and without looking, this would report success for
    a certificate nothing trusts.
    """
    if not mode:
        print(f"  {MIDDOT} The appliance is not running — this takes effect at the next `embabel up`.")
        return True
    print("  " + dim("Restarting the app so it starts with this trust…"))
    if _compose(mode, "up", "-d", "--force-recreate", "--no-deps", MODE_SERVICE[mode], capture=True).returncode != 0:
        print("  " + warn("Could not restart the app. Run `embabel up`, then `embabel trust list`."))
        return False
    wanted = {a.fingerprint for a in expect}
    if not wanted:
        return True
    deadline = time.monotonic() + IMPORT_WAIT_SECONDS
    while time.monotonic() < deadline:
        container = find_mode_container(mode)
        if container and _imported(container, wanted) == wanted:
            print(f"  {TICK} The appliance trusts it. It is starting up now — `embabel status` says when it is ready.")
            return True
        time.sleep(2)
    print("  " + warn("The app restarted but does not hold the certificate."))
    print("  This image predates certificate import. Run `embabel upgrade`, and it is picked up from certs/.")
    return False
