"""Browser-free tests that one session's browser serves one request at a time.

A browser has one page. Two requests naming the same session used to drive the
same Chrome WebDriver at once, so one could navigate under the other and answer
it with the wrong page; the stealth engine had a lock on its context from the
start. The lock now lives on the session, so both engines follow the same rule.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_session_locking
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

from dtos import V1RequestBase
from engines import chrome_engine, stealth_engine
from engines.chrome_engine import ChromeEngine
from engines.stealth_engine import StealthEngine
from sessions import SessionStore


class _Overlap:
    """Records whether a second solve started before the first one ended."""

    def __init__(self):
        self.inside = 0
        self.overlapped = False
        self.first_in = threading.Event()
        self.may_finish = threading.Event()

    def enter(self):
        self.inside += 1
        if self.inside > 1:
            self.overlapped = True
        if self.inside == 1:
            self.first_in.set()
            # Hold the browser until the second request has had its chance.
            self.may_finish.wait(2)
        self.inside -= 1


def _chrome_engine(overlap):
    store = SessionStore(build=lambda proxy=None: MagicMock(), teardown=lambda d: None)
    store.create("s")
    engine = ChromeEngine(sessions=store)

    def solve(req):
        with patch.object(chrome_engine, "_apply_timezone", lambda *_a: None), \
                patch.object(ChromeEngine, "_evil_logic", lambda *_a: overlap.enter()):
            engine.solve(req, "GET", 5.0)
    return solve


def _stealth_engine(overlap):
    engine = StealthEngine.__new__(StealthEngine)
    engine._launch_budget = threading.local()
    engine._runtime = MagicMock()
    engine._runtime.run = lambda coro, timeout=None: (coro.close(), overlap.enter())[1]
    engine._sessions = SessionStore(build=lambda proxy=None: MagicMock(), teardown=lambda c: None)
    engine._sessions.create("s")

    def solve(req):
        engine.solve(req, "GET", 5.0)
    return solve


class OneRequestAtATime(unittest.TestCase):

    def assert_serialised(self, build):
        overlap = _Overlap()
        solve = build(overlap)
        req = V1RequestBase({"url": "https://example-site.tld/", "session": "s"})

        first = threading.Thread(target=solve, args=(req,))
        first.start()
        overlap.first_in.wait(2)
        second = threading.Thread(target=solve, args=(req,))
        second.start()
        second.join(0.5)
        overlap.may_finish.set()
        first.join(2)
        second.join(2)

        self.assertFalse(overlap.overlapped)

    def test_neither_engine_runs_two_requests_on_one_session_at_once(self):
        for name, build in (("chrome", _chrome_engine), ("stealth", _stealth_engine)):
            with self.subTest(engine=name):
                self.assert_serialised(build)
