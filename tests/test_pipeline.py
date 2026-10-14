import tempfile
import unittest

import requests

from mkay_radar.cli import run_collectors, run_should_fail
from mkay_radar.models import Opportunity, canonicalize_url
from mkay_radar.sources.airdrops_io import AirdropsIoCollector
from mkay_radar.sources.airdrop_alert import AirdropAlertCollector
from mkay_radar.sources.cryptojobslist import CryptoJobsListCollector
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

    def test_run_fails_only_when_every_source_failed(self) -> None:
        self.assertFalse(run_should_fail(2, []))                    # all fine
        self.assertFalse(run_should_fail(2, ["Airdrops.io"]))       # one blocked: later steps must still run
        self.assertTrue(run_should_fail(2, ["Airdrops.io", "AirdropAlert"]))  # nothing collected
        self.assertTrue(run_should_fail(1, ["Airdrops.io"]))        # single-source command that failed

    def test_failed_source_emits_github_actions_warning(self) -> None:
        import contextlib, io, os
        class FailingCollector:
            def collect(self):
                raise requests.HTTPError("403 Client Error: Forbidden\nfor url: https://airdrops.io/")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(f"sqlite:///{directory}/radar.db")
            store.initialize()
            buffer = io.StringIO()
            previous = os.environ.get("GITHUB_ACTIONS")
            os.environ["GITHUB_ACTIONS"] = "true"
            try:
                with contextlib.redirect_stdout(buffer):
                    run_collectors([("Airdrops.io", FailingCollector())], store)
            finally:
                if previous is None:
                    os.environ.pop("GITHUB_ACTIONS", None)
                else:
                    os.environ["GITHUB_ACTIONS"] = previous
            store.connection.close()
        warning_lines = [line for line in buffer.getvalue().splitlines() if line.startswith("::warning")]
        self.assertEqual(len(warning_lines), 1)
        self.assertIn("Airdrops.io failed", warning_lines[0])
        self.assertIn("403", warning_lines[0])


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

    def test_cryptojobslist_api_keeps_remote_jobs(self) -> None:
        payload = {"jobs": [{
            "jobTitle": "Solidity Engineer",
            "companyName": "Example Protocol",
            "canonicalURL": "https://cryptojobslist.com/jobs/example",
            "description": "Build smart contracts.",
            "tags": ["solidity", "defi"],
            "employmentType": ["FULL_TIME"],
        }]}
        records = CryptoJobsListCollector.parse(payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].category, "JOB")
        self.assertEqual(records[0].project, "Example Protocol")
        self.assertIn("Remote", records[0].requirements)

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
            outcomes, failures = run_collectors(
                [("Working", WorkingCollector()), ("Failing", FailingCollector())], store
            )
            self.assertEqual(failures, ["Failing"])
            self.assertEqual(outcomes.get("created"), 1)
            self.assertEqual(len(store.review_queue(10)), 1)
            store.connection.close()

    def test_bad_source_format_does_not_block_the_others(self) -> None:
        class WorkingCollector:
            def collect(self):
                return [opportunity("https://airdrops.io/still-working/")]

        class BadFeedCollector:
            def collect(self):
                raise ValueError("The feed is not valid XML")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(f"sqlite:///{directory}/radar.db")
            store.initialize()
            outcomes, failures = run_collectors(
                [("Working", WorkingCollector()), ("Bad feed", BadFeedCollector())], store
            )
            self.assertEqual(failures, ["Bad feed"])
            self.assertEqual(outcomes.get("created"), 1)
            store.connection.close()
