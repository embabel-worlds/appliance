"""The command surface: every verb, its arguments, and the examples in --help.

Separate from the verbs themselves because it is the one place that has to know about
ALL of them, and separate from the launcher because `embabel completion` reads it to
generate shell completions — a script cannot import a script.
"""

from __future__ import annotations
import argparse

from .cli import _emit, _subparsers, current_mode, resolve_instance, resolved_mode, run_setup, s
from .clicare import (cmd_backup, cmd_bugreport, cmd_completion, cmd_instances,
                      cmd_reset_password, cmd_restore, cmd_uninstall, cmd_upgrade,
                      cmd_version, cmd_where)
from .clidata import (cmd_agents, cmd_contract, cmd_realms, cmd_sample, cmd_sandbox,
                      cmd_scenario)
from .clirun import (cmd_doctor, cmd_down, cmd_logs, cmd_open, cmd_prune, cmd_ps,
                     cmd_status, cmd_up)


EXAMPLES = {'up': 'embabel up\n  embabel up --me            the personal-assistant door\n  embabel up --fresh         delete everything first, then set up again', 'logs': "embabel logs -f            follow the appliance's own log\n  embabel logs neo4j         a particular service\n  embabel logs --tail 500", 'backup': 'embabel backup             to ~/embabel-backups\n  embabel backup /Volumes/ext\n  embabel backup --list', 'restore': 'embabel restore ~/embabel-backups/embabel-backup-appliance-2026-08-23-1118', 'realms': 'embabel realms link ~/src  point it at your realm checkouts\n  embabel realms list', 'open': "embabel open               this appliance's front door\n  embabel open graph         the Neo4j browser", 'version': 'embabel version\n  embabel version --json     for a script', 'bugreport': 'embabel bugreport          to ~/embabel-backups\n  embabel bugreport --all-logs   full logs; read before sending', 'completion': 'source <(embabel completion bash)\n  embabel completion zsh > "${fpath[1]}/_embabel"'}


def build_parser() -> argparse.ArgumentParser:
    """The parser, built where `completion` can also reach it. Every verb, flag and
    choice a shell offers is read back out of this — one definition, not two.

    THE INSTANCE FLAG HIDES ITSELF. Somebody with one appliance — which is nearly
    everybody — should never meet --instance, never invent a name, and never see a
    verb for managing a thing they have one of. So the flag and the `instances`
    verb are added always, because a hidden flag must still WORK, but they are
    suppressed from --help until a second instance exists. The machinery arrives
    the moment it means something and not before.
    """
    several = len(s.installed_instances()) > 1
    parser = argparse.ArgumentParser(
        prog="embabel",
        description="The Embabel appliance: a knowledge graph, an agent runtime, and the realms you install into it.",
        epilog="Full reference: https://worlds.embabel.com/cli/",
    )
    # NOT required: `embabel` on its own is what someone types when they want to
    # know where things stand, and answering that with an argparse usage error is
    # a wasted first impression. It shows status and what to do next.
    parser.add_argument("--instance", metavar="NAME",
                        help="which appliance to act on" if several else argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    def mode_flags(p):
        # NEITHER is "the default" any more: the default is whatever this machine
        # is already running or was last set up as. These name the OTHER door.
        p.add_argument("--worlds", dest="mode", action="store_const", const="worlds",
                       help="the world runtime and its console")
        p.add_argument("--me", dest="mode", action="store_const", const="me",
                       help="the personal-assistant door")

    p = sub.add_parser("up", help="start the appliance and finish setup (safe to re-run)")
    mode_flags(p)
    p.add_argument("--fresh", action="store_true", help="DELETE all data first, then start over")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("status", help="what is running, what is still downloading, where to go")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("ps", help="what this appliance has on the host, including code sandboxes")
    p.add_argument("--json", action="store_true", help="the same, as JSON")
    p.set_defaults(func=cmd_ps)

    p = sub.add_parser("doctor", help="check everything that has ever gone wrong for somebody")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("logs", help="follow the appliance's log")
    mode_flags(p)
    p.add_argument("service", nargs="?", help="a compose service; defaults to the appliance itself")
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("--tail", type=int, default=200)
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("open", help="open a surface in your browser")
    p.add_argument("what", nargs="?", choices=["console", "graph", "dashboards", "me"],
                   help="default: this appliance's front door — the console (worlds) or the assistant (me)")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("realms", help="realm checkouts on this machine")
    rsub = p.add_subparsers(dest="realms_command", required=True)
    rl = rsub.add_parser("link", help="point the appliance at the directory your checkouts live IN")
    rl.add_argument("directory")
    rsub.add_parser("list", help="which realms the appliance can see")
    p.set_defaults(func=cmd_realms)

    p = sub.add_parser("version", help="which appliance this is: tag, digest, commit, checkout")
    mode_flags(p)
    p.add_argument("--json", action="store_true", help="the same four layers, as JSON")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("backup", help="copy everything the appliance knows to a folder")
    p.add_argument("directory", nargs="?",
                   help=f"where backups live; default {s.DEFAULT_BACKUP_DIR}")
    p.add_argument("--list", action="store_true", help="what backups are already there")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="put a backup back, REPLACING what is here")
    p.add_argument("directory", help="a backup folder made by `embabel backup`")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("sample", help="fictional records: load, list, remove, export")
    sp = p.add_subparsers(dest="sample_command")
    a = sp.add_parser("add", help="load a sample set (a name, a file, or gh:owner/repo)")
    a.add_argument("source", help="e.g. hubspot-demo, ./my-set.json, gh:me/my-samples")
    a.set_defaults(func=cmd_sample)
    a = sp.add_parser("remove", help="remove one sample set and nothing else")
    a.add_argument("name")
    a.set_defaults(func=cmd_sample)
    a = sp.add_parser("list", help="what sample data is loaded")
    a.set_defaults(func=cmd_sample)
    a = sp.add_parser("clear", help="remove ALL sample data from this world")
    a.add_argument("--yes", action="store_true", help="skip the confirmation")
    a.set_defaults(func=cmd_sample)
    a = sp.add_parser("export", help="write records out as a set somebody else can load")
    a.add_argument("--labels", help="comma-separated, e.g. Company,Deal")
    a.add_argument("--source", help="only what one sample set loaded — the precise selector")
    a.add_argument("--name", default="export", help="what to call the set")
    a.add_argument("--realm", required=True, help="the realm whose types these belong to")
    a.add_argument("--limit", type=int, default=500)
    a.add_argument("--with-values", action="store_true",
                   help="include the real values and ids; read it before sending it")
    a.add_argument("-o", "--output", help="write here instead of stdout")
    a.set_defaults(func=cmd_sample)
    p.set_defaults(func=cmd_sample, sample_command=None)

    p = sub.add_parser("contract", help="data contracts: draft one for a saved view")
    cp = p.add_subparsers(dest="contract_command")
    a = cp.add_parser("generate", help="draft an ODCS contract describing a view's output")
    a.add_argument("--view", required=True, help="the saved view to describe")
    a.add_argument("--sample", action="store_true",
                   help="run the view once, under a row cap, to infer types (reads data)")
    a.add_argument("--save", action="store_true", help="write the draft into the world")
    a.add_argument("--bind", action="store_true",
                   help="also pin the view to it in observe mode (implies --save)")
    a.add_argument("--output", help="write the contract YAML to a file instead of printing it")
    a.set_defaults(func=cmd_contract)
    p.set_defaults(func=cmd_contract, contract_command=None)

    p = sub.add_parser("scenario", help="put the world in a named state")
    sc = p.add_subparsers(dest="scenario_command")
    a = sc.add_parser("list", help="scenarios on this machine, and which one you are in")
    a.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    a.set_defaults(func=cmd_scenario)
    a = sc.add_parser("run", help="bring the world to a scenario's declared state")
    a.add_argument("name")
    a.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    a.set_defaults(func=cmd_scenario)
    a = sc.add_parser("capture", help="write the world's current state out as a scenario")
    a.add_argument("name")
    a.add_argument("--description", help="one line, shown in `scenario list`")
    a.add_argument("--force", action="store_true", help="replace an existing scenario file")
    a.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    a.set_defaults(func=cmd_scenario)
    a = sc.add_parser("next", help="the scenario after the one you are in")
    a.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    a.set_defaults(func=cmd_scenario)
    p.set_defaults(func=cmd_scenario, scenario_command=None, dry_run=False)

    p = sub.add_parser("sandbox", help="the code-mode sandbox, and how to make it yours")
    sb = p.add_subparsers(dest="sandbox_command")
    a = sb.add_parser("show", help="which sandbox image this appliance uses, and why")
    a.set_defaults(func=cmd_sandbox)
    a = sb.add_parser("build", help="build sandbox/Dockerfile and use it")
    a.add_argument("--file", help="a Dockerfile elsewhere")
    a.add_argument("--tag", default="embabel-sandbox:local", help=argparse.SUPPRESS)
    a.add_argument("--no-cache", action="store_true", help="rebuild every layer")
    a.set_defaults(func=cmd_sandbox)
    a = sb.add_parser("reset", help="go back to the shipped image")
    a.set_defaults(func=cmd_sandbox)
    p.set_defaults(func=cmd_sandbox, sandbox_command=None)

    p = sub.add_parser("agents", help="re-point Claude Code and Codex at this appliance")
    p.add_argument("--show-token", action="store_true",
                   help="print the MCP token for manual client configuration")
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("upgrade", help="pull newer images, keep your data")
    mode_flags(p)
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser("down", help="stop the appliance")
    mode_flags(p)
    p.add_argument("--wipe", action="store_true", help="also DELETE all data (asks)")
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("uninstall", help="remove the appliance and this machine's configuration")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("prune", help="remove code-sandbox containers left behind on the host")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("bugreport", help="one folder to attach to an issue, with no secrets in it")
    p.add_argument("directory", nargs="?", help=f"where to write it; default {s.DEFAULT_BACKUP_DIR}")
    p.add_argument("--all-logs", action="store_true",
                   help="FULL logs, not just warnings and errors — they can carry personal data")
    p.set_defaults(func=cmd_bugreport)

    p = sub.add_parser("reset-password", help="forgot the password: recreate the account, keep all data")
    p.set_defaults(func=cmd_reset_password)

    # argparse.SUPPRESS does NOT hide a subparser — it prints the literal
    # "==SUPPRESS==" in the list. Omitting `help` entirely is what keeps a verb
    # out of the help while leaving it callable.
    p = sub.add_parser("instances", **({"help": "every appliance installed here"} if several else {}))
    p.add_argument("--json", action="store_true", help="the same, as JSON")
    p.set_defaults(func=cmd_instances)

    p = sub.add_parser("where", help="print the appliance directory")
    p.set_defaults(func=cmd_where)

    p = sub.add_parser("completion", help="tab completion for your shell")
    p.add_argument("shell", choices=["bash", "zsh", "fish"])
    p.set_defaults(func=cmd_completion)

    # A curated metavar rather than argparse's generated {up,status,ps,…}: with
    # nineteen verbs that line is unreadable, and — the reason it matters here —
    # a subparser suppressed from the help LIST still appears inside it.
    sub.metavar = "<command>"

    # Examples last, in one pass over the parsers that now exist — rather than
    # threaded through twenty add_parser calls. RawDescriptionHelpFormatter
    # because argparse reflows an epilog by default, and a reflowed command is a
    # command that does not run when it is pasted.
    for verb, (subparser, _help) in _subparsers(parser).items():
        lines = EXAMPLES.get(verb)
        if lines:
            subparser.epilog = "Examples:\n  " + lines
            subparser.formatter_class = argparse.RawDescriptionHelpFormatter
    return parser
