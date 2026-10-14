from __future__ import annotations

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

from mkay_radar.sources.airdrops_io import AirdropsIoCollector
from mkay_radar.sources.airdrop_alert import AirdropAlertCollector
from mkay_radar.sources.cryptojobslist import CryptoJobsListCollector
from mkay_radar.storage import open_store
from mkay_radar.telegram import TelegramReviewPublisher, format_review


def store_from_env():
    return open_store(os.getenv("RADAR_DATABASE_URL", "sqlite:///data/radar.db"), os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def run_collectors(collectors, store) -> tuple[dict[str, int], list[str]]:
    """Run each (name, collector) pair independently: one source's failure never
    stops the others, and every other source's records still land in the store.

    Returns (outcomes, names of sources that failed). Callers decide what counts
    as a failed run; see main() for the exit-code rule."""
    outcomes: dict[str, int] = {}
    failures: list[str] = []
    for source_name, collector in collectors:
        source_outcomes: dict[str, int] = {}
        try:
            records = collector.collect()
        # A source is external and can change without warning. Treat parsing
        # errors like network errors, so one bad feed cannot stop every scan.
        except Exception as error:
            print(f"{source_name} run failed, skipping (other sources still ran): {error}")
            if os.getenv("GITHUB_ACTIONS") == "true":
                # Shows as a yellow warning in the Actions UI without failing the step.
                detail = f"{type(error).__name__}: {error}".replace("\r", " ").replace("\n", " ")
                print(f"::warning title={source_name} failed::{detail}")
            failures.append(source_name)
            continue
        for opportunity in records:
            result = store.upsert(opportunity)
            source_outcomes[result] = source_outcomes.get(result, 0) + 1
            outcomes[result] = outcomes.get(result, 0) + 1
        print(f"{source_name} run complete: {source_outcomes or {'no_records': 0}}")
    return outcomes, failures


def run_should_fail(total_sources: int, failures: list[str]) -> bool:
    """A run fails only when every source failed, i.e. nothing could be collected.
    A single blocked source is reported but must not stop later workflow steps."""
    return bool(failures) and len(failures) == total_sources


def main() -> None:
    # Windows consoles may otherwise reject the Telegram preview's emoji.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    parser = argparse.ArgumentParser(description="MKAY Web3 Opportunity Radar")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("collect-airdrops", help="Collect public Airdrops.io guides into the review queue")
    commands.add_parser("collect-airdrop-alert", help="Collect official AirdropAlert RSS listings into the review queue")
    commands.add_parser("collect-cryptojobslist", help="Collect remote jobs through the CryptoJobsList API")
    commands.add_parser("collect-all", help="Collect every approved source into the review queue")
    review = commands.add_parser("review", help="Print or send Telegram-ready review messages")
    review.add_argument("--limit", type=int, default=5)
    review.add_argument("--send", action="store_true", help="Send messages only when Telegram credentials are configured")
    commands.add_parser("process-telegram-actions", help="Apply queued Telegram button taps once")
    commands.add_parser("telegram-listen", help="Keep the Telegram approval and broadcast listener running")
    args = parser.parse_args()
    store = store_from_env()

    collect_commands = {"collect-airdrops", "collect-airdrop-alert", "collect-cryptojobslist", "collect-all"}
    if args.command in collect_commands:
        collectors = []
        if args.command in {"collect-airdrops", "collect-all"}:
            collectors.append(("Airdrops.io", AirdropsIoCollector()))
        if args.command in {"collect-airdrop-alert", "collect-all"}:
            collectors.append(("AirdropAlert", AirdropAlertCollector()))
        if args.command == "collect-cryptojobslist":
            collectors.append(("CryptoJobsList", CryptoJobsListCollector()))
        elif args.command == "collect-all":
            jobs = CryptoJobsListCollector()
            if jobs.configured():
                collectors.append(("CryptoJobsList", jobs))
            else:
                print("CryptoJobsList skipped: set CRYPTOJOBSLIST_API_KEY to enable official API job collection.")
        outcomes, failures = run_collectors(collectors, store)
        print(f"Total run: {outcomes or {'no_records': 0}}")
        if run_should_fail(len(collectors), failures):
            sys.exit(1)
        return

    if args.command == "process-telegram-actions":
        processed = TelegramReviewPublisher().process_updates(store)
        print(f"Processed {processed} Telegram review action(s).")
        return

    if args.command == "telegram-listen":
        print("Listening for Telegram approvals and group-registration commands. Press Ctrl+C to stop.")
        TelegramReviewPublisher().listen(store)
        return

    publisher = TelegramReviewPublisher()
    for opportunity in store.review_queue(args.limit, unsent_only=args.send):
        message = format_review(opportunity)
        if args.send:
            publisher.send_review(opportunity)
            store.mark_review_sent(str(opportunity["id"]))
            print(f"Sent review for opportunity {opportunity['id']}")
        else:
            print("\n" + "=" * 72 + "\n" + message)


if __name__ == "__main__":
    main()
