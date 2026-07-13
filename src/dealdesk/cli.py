"""DealDesk entry point.

Phase 1 exposes one command:

    dealdesk discover        # read-only Source-by-volume tally over the window

It reads the Inbox work queue, tallies candidate Emails by Source, and prints a
Pareto ranking. It makes no write, label, or AI call.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from .auth import build_gmail_service
from .config import Config
from .cutoff import resolve_cutoff
from .discovery import tally_sources
from .gmail_gateway import GmailGateway
from .query import build_work_queue_query


def _cmd_discover(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    today = date.fromisoformat(args.today) if args.today else date.today()
    cutoff = resolve_cutoff(config, today)
    query = build_work_queue_query(cutoff, config.bucket_labels)

    print(f"Inbox:  {config.inbox_address}")
    print(f"Cutoff: {cutoff:%Y-%m-%d}  (on/after; discovery is read-only)")
    print(f"Query:  {query}")
    print()

    service = build_gmail_service(config)
    gateway = GmailGateway(service)
    messages = gateway.fetch_work_queue(cutoff, config.bucket_labels)

    tally = tally_sources(messages)
    total = sum(sc.count for sc in tally)
    print(f"Candidate Emails in window: {total}")
    if not tally:
        return 0

    print()
    print(f"{'Source':<40} {'Count':>6} {'Share':>7} {'Cumul.':>7}")
    print("-" * 63)
    for sc in tally:
        print(f"{sc.source:<40} {sc.count:>6} {sc.pct:>6.1f}% {sc.cumulative_pct:>6.1f}%")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dealdesk", description=__doc__)
    parser.add_argument(
        "--config", default=None, help="Path to dealdesk.toml (defaults to config/dealdesk.toml)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser(
        "discover", help="Read-only Source-by-volume tally over the activation window"
    )
    discover.add_argument(
        "--today",
        default=None,
        help="Override 'today' (ISO date) for the rolling cutoff; useful for testing",
    )
    discover.set_defaults(func=_cmd_discover)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return args.func(args)
    except RuntimeError as exc:
        # Expected operator-facing failures (e.g. missing credential) — report
        # cleanly instead of dumping a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
