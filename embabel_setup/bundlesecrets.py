"""What never goes into a bundle, and the check that refuses one if anything secret got in.

TWO LAYERS, because either alone has failed somewhere before.
  - BY NAME: files and directories the server writes credentials into are never selected
    (SECRET_PATHS, SECRET_NAMES). This is the list a person can read and argue with.
  - BY CONTENT: every file that IS selected is scanned for the shapes credentials take —
    a password hash, an API key the server minted, a provider key, a private key, an OAuth
    token — before a byte of it is written. A match stops the export and names the file,
    never the value. A new secret-bearing file the list above does not know yet is caught
    here, the first time anyone exports one, instead of in somebody's support ticket.

The patterns are deliberately the formats, not the values: nothing here knows a secret.
"""

from __future__ import annotations
import re
import shlex

# Paths under /data/embabel/assistant that hold credentials, relative, as globs the helper's
# find understands. Filled from the server's writers (see the module docstring for why a
# path-only list is not enough on its own).
SECRET_PATHS = (
    "admin/.credentials.yml",          # password hashes and the API keys' digests
    "admin/providers.env",             # provider keys, and the MCP token setup minted
    "admin/.setup-token",              # the first-run token
)

# Names that are never selected wherever they are: what a secret file is called.
SECRET_NAMES = (
    "*.env", ".env*", "secrets*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "id_ed25519*", "*credential*", "*token*", "oauth-apps.yml", "*.kdbx",
)

# The shapes a credential takes inside a file. Each is (what, pattern); the first group, if
# any, is never shown.
SECRET_SHAPES = (
    ("a bcrypt password hash", re.compile(rb"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}")),
    ("a Spring-encoded password", re.compile(rb"\{(?:bcrypt|pbkdf2|scrypt|argon2)\}\S{20,}")),
    ("an appliance API key", re.compile(rb"\bemb_[A-Za-z0-9_-]{20,}")),
    ("an OpenAI key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("an Anthropic key", re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("a Google API key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}")),
    ("a GitHub token", re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}")),
    ("a Slack token", re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("an AWS access key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("a private key", re.compile(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("an OAuth refresh token field", re.compile(rb"[\"']?refresh_?token[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._/-]{20,}", re.I)),
    ("a password in a connection URL", re.compile(rb"[A-Za-z][A-Za-z0-9+.-]*://[^/ \t\n:@\"']+:[^/ \t\n@\"']+@")),
    ("a password in a JDBC URL", re.compile(rb"jdbc:[^ \t\n\"']*[;?&]password=[^;& \t\n\"']+", re.I)),
    ("a client secret field", re.compile(rb"[\"']?client_?secret[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._/-]{12,}", re.I)),
)

# The script the helper container runs to scan the selected files: a grep per shape, which
# prints only file names. Kept in Python (the shapes above) and handed over as ERE patterns.
_GREP_SAFE = re.compile(rb"\\b")


def shapes_in(data: bytes) -> list[str]:
    """What credential shapes [data] holds, by name; for what the CLI writes itself rather than selects."""
    return [what for what, pattern in SECRET_SHAPES if pattern.search(data)]


def grep_patterns() -> list[tuple[str, str]]:
    """The shapes as extended regular expressions for grep -E (which has no \\b; word edges
    are written out), paired with what each one is."""
    out = []
    for what, pattern in SECRET_SHAPES:
        text = pattern.pattern.decode()
        if text.endswith(r"\b"):
            text = text[:-2] + r"($|[^A-Za-z0-9_])"
        text = text.replace(r"\b", r"(^|[^A-Za-z0-9_])").replace(r"\S", r"[^[:space:]]").replace(r"\d", "[0-9]").replace(r" \t\n", "[:space:]")
        text = text.replace("(?:", "(")
        out.append((what, text))
    return out


def scan_script(root: str, list_file: str) -> str:
    """A shell script for the helper container: for each shape, the selected files under
    [root] (listed in [list_file], one per line) that contain it, as `<what>\\t<file>`.
    Case-insensitive where the shape says so; never prints a matching line."""
    lines = [f"cd {root} || exit 2"]
    for what, ere in grep_patterns():
        flag = "-i" if what.startswith(("an OAuth", "a client", "a password in a JDBC")) else ""
        safe_what = what.replace("'", "")
        lines.append(
            f"tr '\\n' '\\0' < {list_file} | xargs -0 -r grep -I -l -E {flag} -e {shlex.quote(ere)} 2>/dev/null"
            f" | sed 's|^|{safe_what}\\t|'"
        )
    return "\n".join(lines)
