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
from .calculator_gateway import CalculatorGateway
from .config import Config
from .cutoff import resolve_cutoff
from .deal_input import should_feed
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
    # Same exclusion as the live queue: without it DealDesk's own notifications
    # show up in the Pareto as a top "Source", which is the one Source the
    # Template work must never be aimed at.
    query = build_work_queue_query(
        cutoff, config.bucket_labels, self_addresses=config.self_addresses
    )

    print(f"Inbox:  {config.inbox_address}")
    print(f"Cutoff: {cutoff:%Y-%m-%d}  (on/after; discovery is read-only)")
    print(f"Query:  {query}")
    print()

    service = build_gmail_service(config)
    gateway = GmailGateway(service)
    messages = gateway.fetch_work_queue(
        cutoff, config.bucket_labels, self_addresses=config.self_addresses
    )

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
    calculator = None
    if not args.dry_run and not args.no_sheets:
        if not config.triage_spreadsheet_id:
            raise RuntimeError("triage.spreadsheet_id is not set; the Triage Log has nowhere to write.")
        # Only a *live* feed needs a sink. With calculator.enabled = false the
        # missing-id check would be a fatal error about a write we aren't doing.
        if config.calc_enabled and not config.calc_spreadsheet_id:
            raise RuntimeError(
                "calculator.spreadsheet_id is not set; qualifying deals have nowhere to feed."
            )
        sheets_service = build_sheets_service(config)  # one service covers both tabs
        sheets = SheetsGateway(sheets_service, config.triage_spreadsheet_id, config.triage_tab)
        if config.calc_enabled:
            calculator = CalculatorGateway(
                sheets_service, config.calc_spreadsheet_id, config.calc_tab
            )

    # --no-notify suppresses Deal Notifications + Daily Digest by withholding the
    # recipient (the orchestrator already skips both when there is no `to`).
    notify_to = "" if args.no_notify else config.notify_to

    mode = "DRY RUN — no writes or labels" if args.dry_run else "live — writes Triage Log, feeds Calculator, applies labels"
    if not args.dry_run and args.no_sheets:
        mode = "live labels only — applies labels, NO sheet writes (Triage Log + Calculator skipped)"
    if not config.calc_enabled:
        mode += " · Calculator feed OFF (calculator.enabled = false)"
    if not args.dry_run and args.no_notify:
        mode += " · notifications DISABLED"
    if args.no_ai:
        mode += " · AI fallback DISABLED (heuristics only)"
    print(f"Inbox:  {config.inbox_address}")
    print(f"Cutoff: {cutoff:%Y-%m-%d}  ({mode})")
    print()

    if not args.dry_run and not notify_to:
        print(
            "warning: no Deal Notifications or Daily Digest will be sent "
            "(notify.to unset or --no-notify).",
            file=sys.stderr,
        )

    orchestrator = Orchestrator(
        gmail,
        sheets,
        ladder,
        config.buybox,
        dry_run=args.dry_run,
        calculator=calculator,
        feed_enabled=config.calc_enabled,
        assumptions=config.assumptions,
        notify_to=notify_to,
        notify_from=config.notify_from,
        notify_label=config.notify_label,
        notify_deal_cc=config.notify_deal_cc,
        calc_link=config.calc_link,
        today=today,
        escalate_after_days=config.escalate_after_days,
        self_addresses=config.self_addresses,
    )
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
    if ai.calls:
        print(
            f"AI tokens: {ai.input_tokens:,} in + {ai.output_tokens:,} out "
            f"over {ai.calls} call(s) [{config.ai_model}]"
        )

    deals_fed = sum(r.deals_fed for r in results)
    if not config.calc_enabled:
        print(
            f"Calculator: feed DISABLED (calculator.enabled = false) — 0 deals written to "
            f"{config.calc_tab}; qualifying deals are in the Triage Log + notifications only"
        )
    else:
        fed_verb = "would feed" if (args.dry_run or args.no_sheets) else "fed"
        print(f"Calculator: {fed_verb} {deals_fed} calc-ready deal(s) to {config.calc_tab}")

    # Report suppressed writes even with the feed off (they'd be 0) — a deal the
    # operator expected in the Calculator and can't find must be accounted for
    # somewhere, not silently dropped.
    duplicates = sum(r.feed_duplicates for r in results)
    if duplicates:
        print(
            f"Calculator: skipped {duplicates} duplicate feed(s) — same address at the "
            "same price is already in the sheet (a re-send at a new price still feeds)"
        )

    resends = sum(r.resends for r in results)
    if resends:
        print(f"Re-sends: {resends} Property(s) flagged as a possible re-send (soft flag; rows are never merged)")

    notified = sum(r.notified for r in results)
    if args.dry_run:
        print(f"Notifications: would send {notified} Deal Notification(s) + 1 Daily Digest")
    elif notify_to:
        print(f"Notifications: sent {notified} Deal Notification(s) + 1 Daily Digest to {notify_to}")
    else:
        print("Notifications: skipped (--no-notify or notify.to not set)")

    if args.report:
        _write_report(args.report, results, ai, config)
        print(f"Report written: {args.report}")
    return 0


def _write_report(path: str, results, ai, config) -> None:
    """Serialize the run to JSON for downstream tooling (the batch-triage skill).
    Carries everything the deterministic-metrics report needs so the consumer
    never has to re-parse human-facing text: per-email bucket + AI flag + the
    exact fallback trigger (missing must-haves), per-Property verdicts/reasons,
    and token totals for costing."""
    import json
    from collections import Counter

    counts = Counter(r.bucket.value for r in results)

    ai_by_bucket: dict[str, dict[str, int]] = {}
    for r in results:
        slot = ai_by_bucket.setdefault(r.bucket.value, {"total": 0, "ai": 0})
        slot["total"] += 1
        if r.used_ai:
            slot["ai"] += 1

    # Rank *why* the fallback fired: the set of must-haves the heuristics missed.
    fallback_reasons: Counter[str] = Counter()
    for r in results:
        if r.used_ai:
            key = " + ".join(r.ai_missing_fields) if r.ai_missing_fields else "unknown"
            fallback_reasons[key] += 1

    emails = [
        {
            "bucket": r.bucket.value,
            "used_ai": r.used_ai,
            "ai_missing_fields": list(r.ai_missing_fields),
            "sender": _fmt_sender(r.sender),
            "subject": r.subject or "",
            "error": r.error,
            "properties": [
                {
                    "address": str(fields.get("address", "")) or None,
                    "verdict": ev.verdict.value,
                    "calc_ready": ev.calc_ready,
                    "fed": should_feed(ev),
                    "reasons": list(ev.reasons),
                }
                for fields, ev in r.properties
            ],
        }
        for r in results
    ]

    report = {
        "model": config.ai_model,
        "emails_processed": len(results),
        "counts": dict(counts),
        "ai_used": sum(1 for r in results if r.used_ai),
        "ai_calls": ai.calls,
        "ai_tokens": {"input": ai.input_tokens, "output": ai.output_tokens},
        "ai_by_bucket": ai_by_bucket,
        "fallback_reasons": dict(fallback_reasons.most_common()),
        "emails": emails,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)


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
            fed = "FEED" if should_feed(ev) else "    "
            reasons = "; ".join(ev.reasons)
            print(f"       #{i} {fed} {ev.verdict.value:<12} {calc:<14} {address[:30]:<30} | {reasons}")
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
    run.add_argument(
        "--no-sheets",
        action="store_true",
        help="Live run that applies labels but skips ALL sheet writes (Triage Log + Calculator). "
        "Warning: labeled Emails are marked processed with no record, so a later full run skips them.",
    )
    run.add_argument(
        "--no-notify",
        action="store_true",
        help="Suppress Deal Notifications and the Daily Digest on a live run",
    )
    run.add_argument(
        "--report",
        default=None,
        metavar="PATH",
        help="Write a structured JSON report of the run (per-email verdicts, AI usage, "
        "fallback trigger reasons, token totals) to PATH — for tooling and skills",
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
