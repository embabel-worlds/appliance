#!/usr/bin/env python3
"""A company certificate authority is trusted by putting it in certs/, and only an authority is.

WHY THIS EXISTS. An appliance on a network that signs with its own authority could not
reach its model gateway: the machine trusted the authority and the container did not, so
the address worked from the terminal and failed from the appliance. `embabel trust add`
is the answer, and it is only an answer if four things hold:

  - what lands in certs/ is the AUTHORITY. A server's own certificate handed over by
    mistake is refused rather than stored, because trusting it works until it is renewed
    and then fails with no change on this machine to explain why
  - the fingerprint printed is the certificate's own, since it is the one thing a person
    can check against what their IT team gave them
  - the app is RECREATED, not restarted, and the result is checked in the container — an
    image that ignores the folder must not be reported as trusting anything
  - both compose files mount the folder where the image's importer reads it

    python3 scripts/check-trust.py

The two certificates below are fixtures made for this check: an authority and a server
certificate it signed, for names that do not exist. Nothing trusts them.
"""
import os
import ssl
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from embabel_setup import trust  # noqa: E402
from embabel_setup.cliparser import build_parser  # noqa: E402
from embabel_setup.core import SetupError  # noqa: E402

AUTHORITY = b"""-----BEGIN CERTIFICATE-----
MIIDTzCCAjegAwIBAgIUdZnLoNRq8+mXyIfDgdalgBWUgqgwDQYJKoZIhvcNAQEL
BQAwNjEVMBMGA1UECgwMRXhhbXBsZSBDb3JwMR0wGwYDVQQDDBRFeGFtcGxlIFRl
c3QgUm9vdCBDQTAgFw0yNjEwMDYwNzAwMThaGA8yMTI2MDkxMjA3MDAxOFowNjEV
MBMGA1UECgwMRXhhbXBsZSBDb3JwMR0wGwYDVQQDDBRFeGFtcGxlIFRlc3QgUm9v
dCBDQTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAMHvQ6mrD2yVfVON
BnJjWIWKZljrG+AJumEB1kEP+fBz8G40JTE73dEk60OUZVLSStXQzE523hCkw1V7
Vq8FbkFbRncloG22KUzRfabCYtaKHcJs+J6Xw8HOzPpWLVmsiHIVnmT5B+jbDkOe
n5nQwG7Ag1dKw5qiWDvAWo359DvGZgLAaVzN0QeF9XI4796XKYDGbRfkhVFyycNT
BQ68P5QlyJDTwh6bkXYg4t4kQvetjh62MgKIyLALGuxgi8ZpPD2W8fFVMwRZU4/S
5XKYpE7dchCNNtZHo9XBdufJXIaQchFk6GmJfLyGqZofM8jjjKTJn5Dbmq9aSfwa
EMJWrXsCAwEAAaNTMFEwHQYDVR0OBBYEFBDykFEGJHbmb6mCqTGPTTmYxSoyMB8G
A1UdIwQYMBaAFBDykFEGJHbmb6mCqTGPTTmYxSoyMA8GA1UdEwEB/wQFMAMBAf8w
DQYJKoZIhvcNAQELBQADggEBAETtJ0oXiMxj/3MrJqv8mJuA4gnPL2Xxf+wdR0mr
DwSXVzO4eODUkj6BlMhyxF5KEW/fWuxCovCnJlMezfuQhEPO51fvpdNv/LqjCSVz
UjL01FSWE5VnfSAjB0dsD2+Xn54Jmu+OlWpn8m4Q7M7dWO1ewfcROj1ATrzRIU5i
ElIBM3SbqvJIjrO+pZsd/sy3zs0XTkwT2M7+GjVhUlY1xgb86NB26MYHljCwN6jR
DtbiPZhY2b4SI1oH+jkpepexBOTdWRNVjx2iaXQwCEeXXIqnOK94s60foBAEWkhF
XgItkwa6cN1UJbjM8UnsWX2iCjsFXHTNtjBSz1fDXZpU/Pw=
-----END CERTIFICATE-----
"""

SERVER = b"""-----BEGIN CERTIFICATE-----
MIIDUzCCAjugAwIBAgIUZ5+GDdBTu3V0v1e/0f+AuBMS3TkwDQYJKoZIhvcNAQEL
BQAwNjEVMBMGA1UECgwMRXhhbXBsZSBDb3JwMR0wGwYDVQQDDBRFeGFtcGxlIFRl
c3QgUm9vdCBDQTAgFw0yNjEwMDYwNzAwMThaGA8yMTI2MDkxMjA3MDAxOFowHzEd
MBsGA1UEAwwUZ2F0ZXdheS5leGFtcGxlLnRlc3QwggEiMA0GCSqGSIb3DQEBAQUA
A4IBDwAwggEKAoIBAQDFs4MVtpAf3zerL54i/mUb2fFfVaWsaJT/Od5DSa65U8bx
Fppf5PGqoxLN2z1frqK1cCLWQD72f9Et0APzhS4bRKxP/3Z/pIxd0OOPAz5zCSn8
hxCCin3XjVTyRu+kYB9F4lMTLdVRtrMZ/Bm51i0bE0Iz06hC4BLP+HaZLNk9YFEa
Cb7DUz1aUTxtzYEW1a5d6BQregYOIJfdUbbVJM8gsQUNIbEfoTCqcSTQ35LN16J8
XrFT43Qeai565ordKXcFzBCTOwgtX/2dz1BRVdTZnwdaUX4TsTqrq+uxUmcVM4GS
7hoIRqICx+yQGVzNdn7gPZ8Jr1ZulsJ5wBj6HrV7AgMBAAGjbjBsMAkGA1UdEwQC
MAAwHwYDVR0RBBgwFoIUZ2F0ZXdheS5leGFtcGxlLnRlc3QwHQYDVR0OBBYEFAzq
/CjPrQtWS0Kl8AqLV3a/OlO2MB8GA1UdIwQYMBaAFBDykFEGJHbmb6mCqTGPTTmY
xSoyMA0GCSqGSIb3DQEBCwUAA4IBAQA8CJNzlkFq0UnPjTuWxUuKcP4NZiQDHhEA
qET0VKBQDmASyzPjlG4fd/PO5+O3BUFFIFI58W5Eo5uj5Pllg+FGM7Jkazgbqti7
EVVEYTUxUHjt/z2F/nyq4sSCFanifXEGMTIN5kAC5IDQqh8n2rBgbZWKqytsX4TR
wY7UQ2xNtEwwX771hsCKaIvulvlcCxt/JNjXK01Zu6bh8tk+XTWFveQwZ96F7Rjl
ZKVg3EpOyBt3lhosKE64QA/tWBdpnAOoXH7amlLSYR99NrwhOWiVHlR1t1fUAi8I
Eyypujh6jCV8QL9DEmVzDUl9t/etEpiBjaA8spFEzk4jCljX738E
-----END CERTIFICATE-----
"""

# openssl x509 -noout -fingerprint -sha256, run against AUTHORITY when it was made.
AUTHORITY_FINGERPRINT = "E4:8D:58:11:F8:C0:75:4C:70:0D:60:69:F2:D3:A3:CB:22:1A:A8:20:20:3B:EA:E7:DD:56:16:5D:4B:15:6B:5C"

failures = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


def refused(action) -> str | None:
    try:
        action()
    except SetupError as e:
        return str(e)
    return None


# An authority is read for what a person can check: its name and its fingerprint.
found, skipped = trust.read_authorities(AUTHORITY)
check([a.name for a in found] == ["Example Test Root CA"] and skipped == 0, f"authority: {found}, skipped {skipped}")
check(found[0].fingerprint == AUTHORITY_FINGERPRINT, f"fingerprint is not the certificate's own: {found[0].fingerprint}")

# The chain as a browser or openssl exports it: the authority is kept, the server's own is not.
found, skipped = trust.read_authorities(SERVER + AUTHORITY)
check([a.name for a in found] == ["Example Test Root CA"] and skipped == 1, f"chain: {found}, skipped {skipped}")

# DER is the other form a keychain exports.
found, _ = trust.read_authorities(ssl.PEM_cert_to_DER_cert(AUTHORITY.decode()))
check(len(found) == 1, f"DER authority was not read: {found}")

# Every refusal of a file says how to get the right one: knowing it must be "the
# authority" is no help to somebody who does not know where that is kept.
for label, content in (("not a certificate", b"not a certificate"), ("empty", b"  \n")):
    why = refused(lambda: trust.read_authorities(content))
    check(why is not None, f"a file that is {label} was accepted")
    check(why is not None and "security find-certificate" in why and "openssl s_client" in why and "embabel trust add" in why,
          f"refusing a file that is {label} gives no example of getting the right one: {why}")

with tempfile.TemporaryDirectory() as tmp, patch.object(trust, "CERTS_DIR", os.path.join(tmp, "certs")):
    def source(name: str, content: bytes) -> str:
        path = os.path.join(tmp, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    # A server's own certificate is refused, and says what to bring instead.
    why = refused(lambda: trust.add_trusted(source("gateway.pem", SERVER)))
    check(why is not None and "authority" in why, f"a server certificate alone was stored: {why}")
    check(why is not None and "openssl s_client" in why, f"refusing a server certificate gives no example: {why}")
    why = refused(lambda: trust.add_trusted(os.path.join(tmp, "missing.crt")))
    check(why is not None and "embabel trust add" in why, f"a missing file gives no example: {why}")
    check(trust.trusted() == [], "a refused file left something in certs/")

    # Stored under the importer's suffix and the authority's own name, one certificate to a
    # file: the system trust store skips a file that holds several.
    kept, left_out = trust.add_trusted(source("exported chain.pem", SERVER + AUTHORITY))
    check([name for name, _ in kept] == ["example-test-root-ca.crt"], f"stored as {kept}")
    check(left_out == 1, f"left out {left_out}")
    with open(os.path.join(trust.CERTS_DIR, "example-test-root-ca.crt"), "rb") as f:
        stored = f.read()
    check(stored.count(trust.PEM_BEGIN) == 1, "the server certificate was written to certs/ beside its authority")
    check([n for n, _ in trust.trusted()] == ["example-test-root-ca.crt"], f"trusted() does not list it: {trust.trusted()}")

    # Adding the same authority again is the same file, not a second one.
    trust.add_trusted(source("again.pem", AUTHORITY))
    check(len(trust.trusted()) == 1, f"the same authority was stored twice: {trust.trusted()}")

    # Removed by the name the list shows, with or without the suffix.
    check(trust.remove_trusted("example-test-root-ca") and trust.trusted() == [], "remove by listed name did nothing")
    check(not trust.remove_trusted("example-test-root-ca"), "removing what is not there reported success")
    check(not trust.remove_trusted("../setup.py"), "remove reached outside certs/")

authorities = trust.read_authorities(AUTHORITY)[0]


class Ran:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout, self.returncode = stdout, returncode


def applied(listing: str):
    """apply_trust against a container whose trust store lists [listing]. Returns (result, compose argv)."""
    calls = []
    with patch.object(trust, "_compose", lambda *argv, **_: calls.append(argv) or Ran()), \
            patch.object(trust, "_docker", lambda *argv, **_: Ran(listing)), \
            patch.object(trust, "find_mode_container", lambda mode: "app"), \
            patch.object(trust, "IMPORT_WAIT_SECONDS", 1), \
            patch.object(trust.time, "sleep", lambda _: None), \
            patch("builtins.print", lambda *a, **k: None):
        return trust.apply_trust("worlds", authorities), calls


# Recreated, so a removed authority does not linger in the old container's store.
ok, calls = applied(f"Certificate fingerprint (SHA-256): {AUTHORITY_FINGERPRINT}")
check(ok, "a container holding the certificate was reported as not trusting it")
check(calls == [("worlds", "up", "-d", "--force-recreate", "--no-deps", "worlds")], f"the app was not recreated alone: {calls}")

# An image that ignores the folder is a failure, not a success with nothing behind it.
ok, _ = applied("Certificate fingerprint (SHA-256): 00:11:22")
check(not ok, "an image that did not import the certificate was reported as trusting it")

# Nothing running: nothing to restart, and nothing to fail.
with patch.object(trust, "_compose", lambda *a, **k: failures.append("compose was run with nothing running")), \
        patch("builtins.print", lambda *a, **k: None):
    check(trust.apply_trust(None, authorities), "adding with the appliance down was reported as a failure")

for compose in ("docker-compose-worlds.yml", "docker-compose-me.yml"):
    with open(os.path.join(ROOT, compose)) as f:
        check("- ./certs:/certificates:ro" in f.read(), f"{compose} does not mount certs/ where the importer reads it")

parser = build_parser()

# A path is resolved where the command was typed, not where the launcher then moves to.
check(os.path.isabs(parser.parse_args(["trust", "add", "ca.crt"]).file), "a relative certificate path is left relative")
for argv in (["trust", "add", "ca.crt"], ["trust", "list"], ["trust", "remove", "ca"], ["trust"]):
    check(parser.parse_args(argv).func.__name__ == "cmd_trust", f"`embabel {' '.join(argv)}` does not reach cmd_trust")

if failures:
    print("check-trust: FAILED")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("check-trust: ok")
