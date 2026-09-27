from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from mkay_radar.sources.airdrops_io import AirdropsIoCollector
from mkay_radar.sources.airdrop_alert import AirdropAlertCollector
from mkay_radar.sources.nft_calendar import NftCalendarCollector
from mkay_radar.storage import open_store
from mkay_radar.telegram import TelegramReviewPublisher, format_review


def store_from_env():
    return open_store(os.getenv("RADAR_DATABASE_URL", "sqlite:///data/radar.db"), os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def main() -> None:
    # Windows consoles may otherwise reject the Telegram preview's emoji.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    parser = argparse.ArgumentParser(description="MKAY Web3 Opportunity Radar")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("collect-airdrops", help="Collect public Airdrops.io guides into the review queue")
    commands.add_parser("collect-airdrop-alert", help="Collect official AirdropAlert RSS listings into the review queue")
    commands.add_parser("collect-nft-calendar", help="Collect public NFTCalendar.io whitelist/drop listings into the review queue")
    commands.add_parser("collect-all", help="Collect every approved source into the review queue")
    review = commands.add_parser("review", help="Print or send Telegram-ready review messages")
    review.add_argument("--limit", type=int, default=5)
    review.add_argument("--send", action="store_true", help="Send messages only when Telegram credentials are configured")
    commands.add_parser("process-telegram-actions", help="Apply queued Telegram button taps once")
    commands.add_parser("telegram-listen", help="Keep the Telegram approval and broadcast listener running")
    args = parser.parse_args()
    store = store_from_env()

    collect_commands = {"collect-airdrops", "collect-airdrop-alert", "collect-nft-calendar", "collect-all"}
    if args.command in collect_commands:
        outcomes: dict[str, int] = {}
        collectors = []
        if args.command in {"collect-airdrops", "collect-all"}:
            collectors.append(("Airdrops.io", AirdropsIoCollector()))
        if args.command in {"collect-airdrop-alert", "collect-all"}:
            collectors.append(("AirdropAlert", AirdropAlertCollector()))
        if args.command in {"collect-nft-calendar", "collect-all"}:
            collectors.append(("NFTCalendar.io", NftCalendarCollector()))
        for source_name, collector in collectors:
            source_outcomes: dict[str, int] = {}
            for opportunity in collector.collect():
                result = store.upsert(opportunity)
                source_outcomes[result] = source_outcomes.get(result, 0) + 1
                outcomes[result] = outcomes.get(result, 0) + 1
            print(f"{source_name} run complete: {source_outcomes or {'no_records': 0}}")
        print(f"Total run: {outcomes or {'no_records': 0}}")
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
