import unittest

from mkay_radar.models import Opportunity, canonicalize_url
from mkay_radar.sources.airdrops_io import AirdropsIoCollector
from mkay_radar.storage import SQLiteStore
from mkay_radar.telegram import format_review


def opportunity(url: str = "https://airdrops.io/example/?utm=x") -> Opportunity:
    return Opportunity(title="Example", project="Example", category="AIRDROP", url=url, source="airdrops.io", description="A test opportunity")


class PipelineTests(unittest.TestCase):
    def test_canonical_url_removes_tracking(self) -> None:
        self.assertEqual(canonicalize_url("HTTPS://Airdrops.io/example/?utm=x"), "https://airdrops.io/example")


    def test_sqlite_deduplicates_canonical_url(self) -> None:
        import tempfile
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
