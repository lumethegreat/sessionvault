from __future__ import annotations

"""SessionVault memory plugin CLI.

This module is loaded dynamically by Hermes when `memory.provider == sessionvault`.
It registers a top-level command group:

  hermes sessionvault status
  hermes sessionvault search "query" [--scope default|chat|workspace|global]
  hermes sessionvault doctor

Implementation detail:
- Hermes expects plugin CLIs to call `parser.set_defaults(func=...)`.
"""


def register_cli(parser) -> None:
    """Register `hermes sessionvault ...` CLI subcommands.

    Note: memory plugin CLIs are only registered when memory.provider==sessionvault.
    """

    # Subcommands
    sp = parser.add_subparsers(dest="sessionvault_cmd")
    try:
        sp.required = True  # argparse on py3.11+
    except Exception:
        pass

    sp.add_parser("status", help="Show DB status")

    s = sp.add_parser("search", help="Search memory (scoped by default)")
    s.add_argument("query", help="Search query")
    s.add_argument(
        "--scope",
        default="default",
        choices=["default", "chat", "workspace", "global"],
        help="Search scope",
    )
    s.add_argument("--limit", type=int, default=8, help="Max results")

    sp.add_parser("doctor", help="Run integrity checks")

    # Hermes dispatches via args.func(args)
    parser.set_defaults(func=_handle)


def _handle(args) -> None:
    from plugins.memory import load_memory_provider

    prov = load_memory_provider("sessionvault")
    if not prov:
        print("sessionvault provider not found")
        return

    # Initialize in a synthetic CLI context
    from hermes_constants import get_hermes_home

    prov.initialize(
        session_id="cli-sessionvault",
        hermes_home=str(get_hermes_home()),
        platform="cli",
        agent_context="primary",
        agent_identity="cli",
        agent_workspace="cli",
    )

    cmd = getattr(args, "sessionvault_cmd", "")
    if cmd == "status":
        print(prov.handle_tool_call("sessionvault_status", {}))
    elif cmd == "doctor":
        print(prov.handle_tool_call("sessionvault_doctor", {}))
    elif cmd == "search":
        payload = {
            "query": args.query,
            "scope": args.scope,
            "limit": args.limit,
        }
        print(prov.handle_tool_call("sessionvault_search", payload))
    else:
        print("Unknown subcommand")
