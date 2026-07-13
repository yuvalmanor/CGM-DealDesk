"""DealDesk entry point.

Commands:

    dealdesk discover        # read-only Source-by-volume tally over the window
    dealdesk run             # triage the work queue: extract -> evaluate ->
                             # rollup -> Triage Log -> Bucket label (label-last)
    dealdesk run --dry-run   # extract + evaluate + print; no writes or labels

``discover`` makes no write, label, or AI call. ``run`` writes the Triage Log and
applies Bucket labels (unless ``--dry-run``); it may fall back to the Anthropic
API for extraction.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from .ai_fallback import AnthropicFallback
from .auth import (
    GMAIL_MODIFY_SCOPE,
    GMAIL_READONLY_SCOPE,
    build_gmail_service,
    build_sheets_service,
)
from .config import Config
from .cutoff import resolve_cutoff
from .discovery import tally_sources
from .extraction import ExtractionLadder
from .gmail_gateway import GmailGateway
from .orchestrator import Orchestrator
from .query import build_work_queue_query
from .sheets_gateway import SheetsGateway


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


def _cmd_run(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    today = date.fromisoformat(args.today) if args.today else date.today()
    cutoff = resolve_cutoff(config, today)

    if not config.buybox.fields:
        raise RuntimeError("No Buy Box fields configured; add [[buybox.fields]] to the config.")

    scope = GMAIL_READONLY_SCOPE if args.dry_run else GMAIL_MODIFY_SCOPE
    gmail = GmailGateway(build_gmail_service(config, [scope]))

    import os

    ai = AnthropicFallback(config.ai_model, os.environ.get(config.ai_api_key_env))
    ladder = ExtractionLadder(config.buybox, ai, ai_enabled=not args.no_ai)

    if args.no_ai and not args.dry_run:
        print(
            "warning: --no-ai on a live run files Emails on heuristics alone; unparseable "
            "Emails can be misfiled (e.g. Not-A-Deal) and drop out of the queue. Dry-run recommended.",
            file=sys.stderr,
        )

    sheets = None
    if not args.dry_run:
        if not config.triage_spreadsheet_id:
            raise RuntimeError("triage.spreadsheet_id is not set; the Triage Log has nowhere to write.")
        sheets = SheetsGateway(
            build_sheets_service(config), config.triage_spreadsheet_id, config.triage_tab
        )

    mode = "DRY RUN — no writes or labels" if args.dry_run else "live — writes Triage Log, applies labels"
    if args.no_ai:
        mode += " · AI fallback DISABLED (heuristics only)"
    print(f"Inbox:  {config.inbox_address}")
    print(f"Cutoff: {cutoff:%Y-%m-%d}  ({mode})")
    print()

    orchestrator = Orchestrator(gmail, sheets, ladder, config.buybox, dry_run=args.dry_run)
    results = orchestrator.run(cutoff, config.bucket_labels, limit=args.limit)

    if args.verbose:
        _print_verbose(results)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.bucket.value] = counts.get(r.bucket.value, 0) + 1

    print(f"Emails processed: {len(results)}")
    for bucket, n in sorted(counts.items()):
        print(f"  {bucket:<14} {n}")

    ai_used = sum(1 for r in results if r.used_ai)
    print(f"AI fallback used: {ai_used} of {len(results)} emails")
    return 0


def _print_verbose(results) -> None:
    print("=== per-email ===")
    print(f"  {'':<3}{'Bucket':<14} {'Sender':<28} Subject")
    print(f"  {'':<3}{'-' * 14} {'-' * 28} {'-' * 34}")
    for r in results:
        marker = "AI " if r.used_ai else "   "
        sender = _fmt_sender(r.sender)
        subject = r.subject or "(no subject)"
        print(f"  {marker}{r.bucket.value:<14} {sender:<28.28} {subject[:44]}")
        if r.error:
            print(f"       ! ERROR: {r.error}")
            continue
        for i, (fields, ev) in enumerate(r.properties):
            address = str(fields.get("address", "")) or "(no address)"
            calc = "calc-ready" if ev.calc_ready else "not-calc-ready"
            reasons = "; ".join(ev.reasons)
            print(f"       #{i} {ev.verdict.value:<12} {calc:<14} {address[:32]:<32} | {reasons}")
    print()


def _fmt_sender(raw: str) -> str:
    from email.utils import parseaddr

    _, addr = parseaddr(raw or "")
    return addr or (raw or "(unknown)")


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

    run = sub.add_parser(
        "run", help="Triage the work queue: extract -> evaluate -> rollup -> Triage Log -> label"
    )
    run.add_argument(
        "--today",
        default=None,
        help="Override 'today' (ISO date) for the rolling cutoff; useful for testing",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract, evaluate, and print without writing the Triage Log or applying labels",
    )
    run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N Emails (caps reads and AI cost); useful for a first live test",
    )
    run.add_argument(
        "--verbose",
        action="store_true",
        help="Print each Email's Bucket, whether the AI fallback fired, and per-Property verdict + reasons",
    )
    run.add_argument(
        "--no-ai",
        action="store_true",
        help="Disable the AI fallback (heuristics only) — zero token cost for scouting a large batch",
    )
    run.set_defaults(func=_cmd_run)
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
