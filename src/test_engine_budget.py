"""Browser-free tests for how maxTimeout is spent across engines.

maxTimeout is how long the caller is willing to wait for an answer. It used to
be handed to each engine in turn, so a two-engine fallback could take twice as
long as asked, which is how a client's own timeout fired while Solverr still
believed it was inside the budget.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_engine_budget
"""
import threading
import unittest
from concurrent.futures import TimeoutError as FuturesTimeout
from unittest.mock import MagicMock, patch

import budget
import flaresolverr_service
from dtos import V1RequestBase
from engines import chrome_engine, stealth_engine
from engines.base import SolveResult
from sessions import SessionStore


def _wont_launch(proxy=None):
    raise Exception("Camoufox could not be started")


def _launch_times_out(proxy=None):
    # concurrent.futures' own timeout, which carries no message at all.
    raise FuturesTimeout()

SOLVED = "<html><title>Real page</title>ok</html>"
CHALLENGE_TITLE = "Just a moment..."
CHALLENGE = "<html><title>Just a moment...</title>window._cf_chl_opt</html>"


class FakeEngine:
    """Records the budget it was given and burns the share it is told to.

    Burning the time inside solve is the point: a clock that advances on its own
    schedule cannot tell a per-engine reading of what is left from a single
    reading taken before any engine ran, which is the regression these tests
    exist to catch.
    """

    def __init__(self, name, spends=0.0, response=SOLVED, raises=None):
        self.name = name
        self.spends = spends
        self.response = response
        self.raises = raises
        self.granted = None
        self.clock = [0.0]

    def solve(self, req, method, timeout):
        self.granted = timeout
        self.clock[0] += self.spends
        if self.raises:
            raise Exception(self.raises)
        result = SolveResult()
        result.url = "https://example-site.tld/"
        result.response = self.response
        result.message = "Challenge solved!"
        return result


def resolve(engines, max_timeout=60000):
    """Run the controller over `engines`, on a clock only they move."""
    now = [0.0]
    for engine in engines:
        engine.clock = now

    def monotonic():
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
        first = FakeEngine("chrome", response=CHALLENGE, spends=50.0)
        second = FakeEngine("stealth")

        resolve([first, second])

        self.assertEqual(second.granted, 10.0)

    def test_a_quick_first_engine_leaves_the_fallback_nearly_everything(self):
        first = FakeEngine("chrome", response=CHALLENGE, spends=2.0)
        second = FakeEngine("stealth")

        resolve([first, second])

        self.assertEqual(second.granted, 58.0)

    def test_a_fallback_is_skipped_when_the_budget_is_spent(self):
        first = FakeEngine("chrome", response=CHALLENGE, spends=58.0)
        second = FakeEngine("stealth")

        resolve([first, second])

        self.assertIsNone(second.granted)

    def test_a_spent_budget_still_returns_the_page_the_first_engine_got(self):
        first = FakeEngine("chrome", response=CHALLENGE, spends=58.0)

        res = resolve([first, FakeEngine("stealth")])

        self.assertEqual(res.result.response, CHALLENGE)

    def test_a_spent_budget_reports_the_first_engine_error_not_a_timeout(self):
        first = FakeEngine("chrome", raises="Cloudflare has blocked this request.", spends=58.0)

        with self.assertRaises(Exception) as caught:
            resolve([first, FakeEngine("stealth")])

        self.assertIn("blocked", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class LaunchInsideTheShare(unittest.TestCase):
    """Getting a browser is part of an engine's share, not extra time beside it.

    Both engines used to start their own clock once the browser was up, so a
    launch that took ten seconds turned a 30 second share into 40, and the
    fallback engine only gets what the first one leaves.
    """

    def test_chrome_gives_the_solve_what_the_launch_left(self):
        import utils
        from engines.chrome_engine import ChromeEngine
        # One clock: budget and the engine both read the same time module. The
        # launch between the two reads costs five of the thirty seconds.
        clock = iter([0.0, 5.0])
        granted = []

        def timed(seconds, func, args):
            granted.append(seconds)
            return "solved"

        with patch.object(chrome_engine.time, "monotonic", lambda: next(clock)),                 patch.object(utils, "get_webdriver", return_value=MagicMock()),                 patch.object(chrome_engine, "_apply_timezone", lambda *_a: None),                 patch.object(chrome_engine, "func_timeout", timed):
            ChromeEngine(sessions=None).solve(
                V1RequestBase({"url": "https://example-site.tld/"}), "GET", 30.0)

        self.assertEqual(granted, [25.0])

    def test_stealth_gives_the_solve_what_the_launch_left(self):
        from engines.stealth_engine import StealthEngine
        # Same one clock: the launch costs eight of the thirty seconds.
        clock = iter([0.0, 8.0, 8.0])
        granted = []
        engine = StealthEngine.__new__(StealthEngine)
        engine._launch_budget = threading.local()
        engine._runtime = MagicMock()

        def run(coro, timeout=None):
            coro.close()
            granted.append(timeout)
            return "solved"

        engine._runtime.run = run
        with patch.object(stealth_engine.time, "monotonic", lambda: next(clock)),                 patch.object(stealth_engine.config, "stealth_start_timeout", lambda: 120.0),                 patch.object(stealth_engine, "StealthContext", lambda _cfg: MagicMock()):
            engine.solve(V1RequestBase({"url": "https://example-site.tld/"}), "GET", 30.0)

        # The launch bound, the solve with what the launch left plus the outer
        # cap, then the teardown's own fixed timeout.
        self.assertEqual(granted[:2], [22.0, 27.0])


class LaunchFailureMessage(unittest.TestCase):
    """Both engines report a browser that will not start the same way."""

    def test_a_session_launch_failure_says_error_solving_the_challenge(self):
        from engines.stealth_engine import StealthEngine
        engine = StealthEngine.__new__(StealthEngine)
        engine._launch_budget = threading.local()
        engine._runtime = MagicMock()
        engine._sessions = SessionStore(build=_wont_launch, teardown=lambda p: None)

        with self.assertRaises(Exception) as caught:
            engine.solve(V1RequestBase({"url": "https://example-site.tld/", "session": "s"}),
                         "GET", 30.0)

        self.assertIn("Error solving the challenge.", str(caught.exception))

    def test_a_session_launch_timeout_reports_the_timeout(self):
        from engines.stealth_engine import StealthEngine
        engine = StealthEngine.__new__(StealthEngine)
        engine._launch_budget = threading.local()
        engine._runtime = MagicMock()
        engine._sessions = SessionStore(build=_launch_times_out, teardown=lambda p: None)

        with self.assertRaises(Exception) as caught:
            engine.solve(V1RequestBase({"url": "https://example-site.tld/", "session": "s"}),
                         "GET", 30.0)

        self.assertIn("Timeout after 30.0 seconds.", str(caught.exception))


class PostSolveSettle(unittest.TestCase):
    """Letting the cleared page settle is inside the share, not on top of it."""

    def test_the_settle_wait_never_outlasts_the_share(self):
        from engine_fakes import StealthHarness, World
        world = World(title=CHALLENGE_TITLE, challenged_for=1)

        StealthHarness().solve(world, timeout=4.0)

        # 4s minus the 3s response margin leaves 1s for both states together.
        self.assertLessEqual(max(t for _s, t in world.settle_waits), 1000)
