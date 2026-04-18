from __future__ import annotations

"""SessionVault memory plugin CLI.

This module is loaded dynamically by Hermes when `memory.provider == sessionvault`.
It registers a top-level command group:

  hermes sessionvault status
  hermes sessionvault search "query" [--scope default|chat|workspace|global]
  hermes sessionvault events [--from <time>] [--to <time>] [--scope ...]
  hermes sessionvault timeline --from <time> [--to <time>] [--scope ...]
  hermes sessionvault lineage [session_id]
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
    s.add_argument("--kind", action="append", default=[], help="Filter raw message kind (repeatable)")
    s.add_argument("--role", action="append", default=[], help="Filter raw message role (repeatable)")
    s.add_argument("--session-id", default="", help="Filter exact session_id")
    s.add_argument("--platform", default="", help="Filter platform")
    s.add_argument("--chat-id", default="", help="Filter chat_id")
    s.add_argument("--thread-id", default="", help="Filter thread_id")

    e = sp.add_parser("events", help="List structured lifecycle events")
    e.add_argument("--from", dest="from_time", help="Inclusive start of time window (epoch or ISO datetime)")
    e.add_argument("--to", dest="to_time", help="Inclusive end of time window (epoch or ISO datetime)")
    e.add_argument(
        "--scope",
        default="default",
        choices=["default", "chat", "workspace", "global"],
        help="Events scope",
    )
    e.add_argument("--limit", type=int, default=25, help="Max results")
    e.add_argument("--event-type", default="", help="Optional event_type filter")

    t = sp.add_parser("timeline", help="List raw messages by created_at range")
    t.add_argument("--from", dest="from_time", required=True, help="Inclusive start of time window (epoch or ISO datetime)")
    t.add_argument("--to", dest="to_time", help="Inclusive end of time window (epoch or ISO datetime)")
    t.add_argument(
        "--scope",
        default="default",
        choices=["default", "chat", "workspace", "global"],
        help="Timeline scope",
    )
    t.add_argument("--limit", type=int, default=25, help="Max results")

    l = sp.add_parser("lineage", help="Show session lineage / continuity")
    l.add_argument("session_id", nargs="?", default="", help="Optional target session_id (defaults to current session)")

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
            "kind": args.kind,
            "role": args.role,
            "session_id": args.session_id,
            "platform": args.platform,
            "chat_id": args.chat_id,
            "thread_id": args.thread_id,
        }
        print(prov.handle_tool_call("sessionvault_search", payload))
    elif cmd == "events":
        payload = {
            "from": args.from_time,
            "to": args.to_time,
            "scope": args.scope,
            "limit": args.limit,
            "event_type": args.event_type,
        }
        print(prov.handle_tool_call("sessionvault_events", payload))
    elif cmd == "timeline":
        payload = {
            "from": args.from_time,
            "to": args.to_time,
            "scope": args.scope,
            "limit": args.limit,
        }
        print(prov.handle_tool_call("sessionvault_timeline", payload))
    elif cmd == "lineage":
        payload = {"session_id": args.session_id}
        print(prov.handle_tool_call("sessionvault_lineage", payload))
    else:
        print("Unknown subcommand")
