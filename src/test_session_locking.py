"""Browser-free tests that one session's browser serves one request at a time.

A browser has one page. Two requests naming the same session used to drive the
same Chrome WebDriver at once, so one could navigate under the other and answer
it with the wrong page; the stealth engine had a lock on its context from the
start. The lock now lives on the session, so both engines follow the same rule.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_session_locking
"""
import threading
import unittest
from contextlib import ExitStack
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
    """(solve, patches) for the Chrome engine on one session.

    The patches are applied once, around both threads: unittest.mock is not
    thread safe, and patching inside each thread left the class patched for
    every test that ran afterwards.
    """
    store = SessionStore(build=lambda proxy=None: MagicMock(), teardown=lambda d: None)
    store.create("s")
    engine = ChromeEngine(sessions=store)
    patches = [patch.object(chrome_engine, "_apply_timezone", lambda *_a: None),
               patch.object(ChromeEngine, "_evil_logic", lambda *_a: overlap.enter())]
    return (lambda req: engine.solve(req, "GET", 5.0)), patches


def _stealth_engine(overlap):
    engine = StealthEngine.__new__(StealthEngine)
    engine._launch_budget = threading.local()
    engine._runtime = MagicMock()
    engine._runtime.run = lambda coro, timeout=None: (coro.close(), overlap.enter())[1]
    engine._sessions = SessionStore(build=lambda proxy=None: MagicMock(), teardown=lambda c: None)
    engine._sessions.create("s")
    return (lambda req: engine.solve(req, "GET", 5.0)), []


class OneRequestAtATime(unittest.TestCase):

    def assert_serialised(self, build):
        overlap = _Overlap()
        solve, patches = build(overlap)
        req = V1RequestBase({"url": "https://example-site.tld/", "session": "s"})

        with ExitStack() as stack:
            for each in patches:
                stack.enter_context(each)
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
