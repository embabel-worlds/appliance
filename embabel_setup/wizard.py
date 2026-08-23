"""The first-run questions: which to ask, in what order, and in whose words.

THIS USED TO LIVE IN THE SERVER. It published an ordered list of steps with
titles, field descriptors and a `nextStep`, and this client rendered whatever it
was handed. The stated benefit was that a new client could not hardcode a stale
flow. The actual cost was that the wording of a question, and the rule about
which questions an operator may skip, were Kotlin constants inside a Docker
image — so changing a sentence meant a rebuild, and the appliance could not
decide that a provider key is optional without the server agreeing.

The line now falls where the knowledge does. The server owns what only it can
know: whether a credential exists, which providers it has accepted, how long a
password must be. This module owns what only the installer knows: that there is
a person at a terminal, what to call things, and what they are allowed to skip.
Anything the flow needs to remember between runs goes to `PUT /setup/state`,
which the server stores and hands back without reading.

The shape below is the same dict the server used to send, because `ask` and
`run_step` already speak it — this is a move, not a redesign. Prose long enough
to need wrapping still belongs in copy/, not here; these are labels.
"""

# Ids double as endpoint paths: `account` posts to /setup/account. Kept identical
# to the server's own constants, which is the actual contract between us now.
ACCOUNT = "account"
PROVIDER = "provider"
MCP = "mcp"

# The installer's note that it offered MCP and was turned down. The server stores
# this without understanding it — which is the point — so a resumed run does not
# ask a second time.
MCP_DECLINED = "mcpDeclined"


def steps(facts: dict) -> list[dict]:
    """Every step, with `satisfied` decided here from the server's facts.

    `facts` is the body of `GET /setup`: hasAccount, providers,
    supportedProviders, mcpTokenExists, minPasswordLength, clientState.
    """
    min_password = facts.get("minPasswordLength") or 8
    supported = facts.get("supportedProviders") or []
    declined_mcp = (facts.get("clientState") or {}).get(MCP_DECLINED) == "yes"
    return [
        {
            "id": ACCOUNT,
            "title": "Create your account",
            "description": "The account you will sign in with. Until this exists, "
                           "nobody can sign in at all.",
            "satisfied": bool(facts.get("hasAccount")),
            "fields": [
                {"name": "username", "label": "Username", "type": "STRING"},
                {"name": "displayName", "label": "Your name", "type": "STRING",
                 "required": False},
                {"name": "password",
                 "label": f"Password (min {min_password} characters)",
                 "type": "SECRET"},
            ],
        },
        {
            "id": PROVIDER,
            "title": "Connect a model provider",
            "description": "Paste a key and we work out whose it is. It is checked "
                           "against the provider now, and stays on this machine.",
            "satisfied": bool(facts.get("providers")),
            "fields": [
                {"name": "apiKey", "label": "API key", "type": "SECRET"},
                # Deliberately last and optional: the key identifies its own
                # provider, so this is an override for the unusual case, not a
                # question every operator has to answer before pasting anything.
                {"name": "provider",
                 "label": "Provider (optional — detected from the key)",
                 "type": "CHOICE", "required": False, "options": supported},
            ],
        },
        {
            "id": MCP,
            "title": "Connect coding agents (MCP)",
            "description": "Lets Claude Code, Codex or any MCP client drive this "
                           "appliance as you. The token stays on this machine and "
                           "becomes active when setup completes and the appliance "
                           "restarts.",
            # A token already exists, or we asked once and were told no. The
            # server knows the first; only we know the second.
            "satisfied": bool(facts.get("mcpTokenExists")) or declined_mcp,
            "fields": [
                {"name": "enable", "label": "Enable MCP access", "type": "CHOICE",
                 "options": ["yes", "no"], "default": "yes"},
            ],
        },
    ]


def pending(facts: dict) -> list[dict]:
    """The steps still worth asking about."""
    return [step for step in steps(facts) if not step["satisfied"]]
