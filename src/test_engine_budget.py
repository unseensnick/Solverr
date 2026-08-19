"""Browser-free tests for how maxTimeout is spent across engines.

maxTimeout is how long the caller is willing to wait for an answer. It used to
be handed to each engine in turn, so a two-engine fallback could take twice as
long as asked, which is how a client's own timeout fired while Solverr still
believed it was inside the budget.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_engine_budget
"""
import unittest
from unittest.mock import patch

import flaresolverr_service
from dtos import V1RequestBase
from engines.base import SolveResult

SOLVED = "<html><title>Real page</title>ok</html>"
CHALLENGE = "<html><title>Just a moment...</title>window._cf_chl_opt</html>"


class FakeEngine:
    """Records the budget it was given and burns the share it is told to."""

    def __init__(self, name, spends=0.0, response=SOLVED, raises=None):
        self.name = name
        self.spends = spends
        self.response = response
        self.raises = raises
        self.granted = None

    def solve(self, req, method, timeout):
        self.granted = timeout
        if self.raises:
            raise Exception(self.raises)
        result = SolveResult()
        result.url = "https://example-site.tld/"
        result.response = self.response
        result.message = "Challenge solved!"
        return result


def resolve(engines, max_timeout=60000, elapsed=None):
    """Run the controller over `engines`, with a clock we drive ourselves."""
    ticks = list(elapsed or [])
    now = [0.0]

    def monotonic():
        # First call sets the deadline; each later call advances by one tick.
        if ticks and len(now) > 1:
            now[0] += ticks.pop(0)
        now.append(now[0])
        return now[0]

    req = V1RequestBase({"cmd": "request.get", "url": "https://example-site.tld/",
                         "maxTimeout": max_timeout})
    with patch.object(flaresolverr_service, "_engine_plan", lambda r: (engines, len(engines) > 1)), \
         patch.object(flaresolverr_service.time, "monotonic", monotonic):
        return flaresolverr_service._resolve_challenge(req, "GET")


class SharedBudget(unittest.TestCase):

    def test_the_only_engine_gets_the_whole_budget(self):
        only = FakeEngine("chrome")

        resolve([only])

        self.assertEqual(only.granted, 60.0)

    def test_a_first_engine_cannot_spend_the_fallback_share(self):
        first = FakeEngine("chrome")

        resolve([first, FakeEngine("stealth")])

        self.assertEqual(first.granted, 30.0)

    def test_the_last_engine_gets_everything_still_unspent(self):
        first = FakeEngine("chrome", response=CHALLENGE)
        second = FakeEngine("stealth")

        resolve([first, second], elapsed=[50.0])

        self.assertEqual(second.granted, 10.0)

    def test_a_quick_first_engine_leaves_the_fallback_nearly_everything(self):
        first = FakeEngine("chrome", response=CHALLENGE)
        second = FakeEngine("stealth")

        resolve([first, second], elapsed=[2.0])

        self.assertEqual(second.granted, 58.0)

    def test_a_fallback_is_skipped_when_the_budget_is_spent(self):
        first = FakeEngine("chrome", response=CHALLENGE)
        second = FakeEngine("stealth")

        resolve([first, second], elapsed=[58.0])

        self.assertIsNone(second.granted)

    def test_a_spent_budget_still_returns_the_page_the_first_engine_got(self):
        first = FakeEngine("chrome", response=CHALLENGE)

        res = resolve([first, FakeEngine("stealth")], elapsed=[58.0])

        self.assertEqual(res.result.response, CHALLENGE)

    def test_a_spent_budget_reports_the_first_engine_error_not_a_timeout(self):
        first = FakeEngine("chrome", raises="Cloudflare has blocked this request.")

        with self.assertRaises(Exception) as caught:
            resolve([first, FakeEngine("stealth")], elapsed=[58.0])

        self.assertIn("blocked", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
