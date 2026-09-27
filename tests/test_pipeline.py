import tempfile
import unittest

import requests

from mkay_radar.cli import run_collectors
from mkay_radar.models import Opportunity, canonicalize_url
from mkay_radar.sources.airdrops_io import AirdropsIoCollector
from mkay_radar.sources.airdrop_alert import AirdropAlertCollector
from mkay_radar.sources.nft_calendar import NftCalendarCollector
from mkay_radar.storage import SQLiteStore
from mkay_radar.telegram import format_review


def opportunity(url: str = "https://airdrops.io/example/?utm=x") -> Opportunity:
    return Opportunity(title="Example", project="Example", category="AIRDROP", url=url, source="airdrops.io", description="A test opportunity")


class PipelineTests(unittest.TestCase):
    def test_canonical_url_removes_tracking(self) -> None:
        self.assertEqual(canonicalize_url("HTTPS://Airdrops.io/example/?utm=x"), "https://airdrops.io/example")


    def test_sqlite_deduplicates_canonical_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(f"sqlite:///{directory}/radar.db")
            store.initialize()
            self.assertEqual(store.upsert(opportunity()), "created")
            self.assertEqual(store.upsert(opportunity("https://airdrops.io/example/")), "unchanged")
            self.assertEqual(len(store.review_queue(10)), 1)
            store.connection.close()


    def test_telegram_preview_is_safe_and_has_link(self) -> None:
        text = format_review({**opportunity().record(), "id": 1})
        self.assertIn("REVIEW", text)
        self.assertIn("Open original guide", text)

    def test_guide_metadata_is_bounded_to_its_list_item(self) -> None:
        html = "<h1>Example Airdrop</h1><ul><li>Chain: Base</li><li>Airdrop Allocation: 10,000 tokens</li></ul><h2>Airdrop Details</h2><p>Useful detail.</p>"
        parsed = AirdropsIoCollector._parse_guide("https://airdrops.io/example/", html)
        self.assertEqual(parsed.chain, "Base")
        self.assertEqual(parsed.reward, "10,000 tokens")

    def test_airdrop_alert_rss_keeps_airdrop_entries(self) -> None:
        feed = b"""<rss><channel><item><title>Example Airdrop</title><link>https://example.com/drop</link><description><![CDATA[<p>Join the campaign.</p>]]></description></item><item><title>Market News</title><link>https://example.com/news</link><description>Not a campaign.</description></item></channel></rss>"""
        records = AirdropAlertCollector.parse(feed)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "airdropalert.com")

    def test_nft_calendar_event_metadata_is_bounded_to_its_label(self) -> None:
        html = (
            '<h1>Example Drop</h1>'
            '<meta name="description" content="A sample NFT drop for tests.">'
            '<p>September 26, 2026 \u2013 October 03, 2026</p>'
            '<h3>Blockchain:</h3><a href="/b/ethereum/">Ethereum</a>'
            '<h3>Marketplace:</h3><a href="/marketplace/example/">Example Market</a>'
        )
        parsed = NftCalendarCollector._parse_event("https://nftcalendar.io/event/example/", html)
        self.assertEqual(parsed.chain, "Ethereum")
        self.assertEqual(parsed.reward, "Example Market")
        self.assertIn("September 26, 2026", parsed.source_status)
        self.assertEqual(parsed.category, "NFT_WHITELIST")

    def test_nft_calendar_excludes_non_event_links(self) -> None:
        html = (
            '<a href="/event/real-drop/">Real Drop</a>'
            '<a href="/events/community/add/">Submit a Drop</a>'
            '<a href="/goto/mexc">Sponsored</a>'
            '<a href="/marketplaces/">Marketplaces</a>'
        )
        urls = NftCalendarCollector._event_urls(html)
        self.assertEqual(urls, ["https://nftcalendar.io/event/real-drop/"])

    def test_one_failing_collector_does_not_block_the_others(self) -> None:
        class WorkingCollector:
            def collect(self):
                return [opportunity("https://airdrops.io/working-source/")]

        class FailingCollector:
            def collect(self):
                raise requests.HTTPError("403 Client Error: Forbidden")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(f"sqlite:///{directory}/radar.db")
            store.initialize()
            outcomes, has_failures = run_collectors(
                [("Working", WorkingCollector()), ("Failing", FailingCollector())], store
            )
            self.assertTrue(has_failures)
            self.assertEqual(outcomes.get("created"), 1)
            self.assertEqual(len(store.review_queue(10)), 1)
            store.connection.close()
