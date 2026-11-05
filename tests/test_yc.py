import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from houselib import cli, yc

COMPANIES = [
    {"name": "Synthetic Users", "batch": "Summer 2023", "status": "Active", "one_liner": "AI user research with synthetic users",
     "long_description": "Interview AI personas instead of recruiting participants.", "tags": ["AI", "User Research"],
     "industries": ["B2B"], "url": "https://www.ycombinator.com/companies/synthetic-users", "website": "https://x.ai"},
    {"name": "CircuitHub", "batch": "Winter 2012", "status": "Active", "one_liner": "On-Demand Electronics Manufacturing",
     "long_description": None, "tags": ["Hardware"], "industries": None, "url": "u2", "website": None},
    {"name": "Dealbot", "batch": "Fall 2024", "status": "Inactive", "one_liner": "AI agents that negotiate prices for you",
     "long_description": "Negotiation copilot.", "tags": ["AI", "Negotiation"], "industries": ["Consumer"], "url": "u3",
     "website": None},
]


class SearchTest(unittest.TestCase):
    def test_ranks_relevant_first_and_skips_irrelevant(self):
        hits = yc.search(COMPANIES, "synthetic user research personas", limit=5)
        self.assertEqual([h["name"] for h in hits], ["Synthetic Users"])

    def test_handles_missing_fields(self):
        hits = yc.search(COMPANIES, "electronics manufacturing", limit=5)
        self.assertEqual(hits[0]["name"], "CircuitHub")

    def test_phrase_and_limit(self):
        hits = yc.search(COMPANIES, "AI agents negotiate", limit=1)
        self.assertEqual([h["name"] for h in hits], ["Dealbot"])

    def test_format_has_batch_status_and_url(self):
        line = yc.format_hit(COMPANIES[2])
        for part in ("Dealbot", "Fall 2024", "Inactive", "negotiate", "u3"):
            self.assertIn(part, line)


class LoadTest(unittest.TestCase):
    def test_uses_fresh_cache_and_refetches_stale(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return json.dumps(COMPANIES).encode()

        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "yc.json"
            self.assertEqual(len(yc.load(cache, fetch=fetch)), 3)
            yc.load(cache, fetch=fetch)
            self.assertEqual(len(calls), 1)  # fresh cache reused
            old = time.time() - 2 * 86400
            os.utime(cache, (old, old))
            yc.load(cache, fetch=fetch)
            self.assertEqual(len(calls), 2)  # stale cache refetched

    def test_stale_cache_used_when_offline(self):
        def boom(url):
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "yc.json"
            cache.write_text(json.dumps(COMPANIES))
            old = time.time() - 5 * 86400
            os.utime(cache, (old, old))
            self.assertEqual(len(yc.load(cache, fetch=boom)), 3)


class CliTest(unittest.TestCase):
    def test_house_yc_subcommand_registered(self):
        args = cli.build_parser().parse_args(["yc", "synthetic", "users", "--limit", "3"])
        self.assertEqual(args.query, ["synthetic", "users"])
        self.assertEqual(args.limit, 3)


if __name__ == "__main__":
    unittest.main()
