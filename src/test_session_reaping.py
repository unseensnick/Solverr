"""Browser-free tests for what the reaper is allowed to close.

A session being solved on is not idle, whatever its timestamp says: quitting the
driver under a live request kills it with an "invalid session id" the caller can
do nothing about. These pin that guard for the Chrome pool, which is the one that
holds a real WebDriver.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_session_reaping
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from sessions import Session, SessionsStorage

LONG_AGO = datetime.now() - timedelta(hours=2)
TTL = timedelta(minutes=30)


def storage_with(*sessions: Session) -> SessionsStorage:
    storage = SessionsStorage()
    for session in sessions:
        storage.sessions[session.session_id] = session
    return storage


def session(session_id: str, last_used: datetime, in_use: int = 0) -> Session:
    return Session(session_id, MagicMock(), LONG_AGO, last_used, in_use)


class ReapIdle(unittest.TestCase):

    def test_an_idle_session_is_reaped(self):
        storage = storage_with(session("idle", LONG_AGO))

        self.assertEqual(storage.reap_idle(TTL), ["idle"])

    def test_a_session_being_solved_on_is_not_reaped(self):
        storage = storage_with(session("solving", LONG_AGO, in_use=1))

        self.assertEqual(storage.reap_idle(TTL), [])

    def test_the_solving_session_survives_in_the_pool(self):
        storage = storage_with(session("solving", LONG_AGO, in_use=1))

        storage.reap_idle(TTL)

        self.assertTrue(storage.exists("solving"))


class EnforceCap(unittest.TestCase):

    def test_the_oldest_idle_session_is_evicted_over_the_cap(self):
        storage = storage_with(session("old", LONG_AGO),
                               session("new", datetime.now()))

        self.assertEqual(storage.enforce_cap(1), ["old"])

    def test_a_session_being_solved_on_is_never_evicted(self):
        storage = storage_with(session("old", LONG_AGO, in_use=1),
                               session("new", datetime.now()))

        self.assertNotIn("old", storage.enforce_cap(1))


class UseCounting(unittest.TestCase):

    def test_a_session_is_free_again_once_its_request_ends(self):
        target = session("s", LONG_AGO)
        storage = storage_with(target)
        storage.begin_use(target)
        storage.end_use(target)

        self.assertEqual(storage.reap_idle(TTL), ["s"])

    def test_concurrent_requests_each_hold_the_session(self):
        target = session("s", LONG_AGO)
        storage = storage_with(target)
        storage.begin_use(target)
        storage.begin_use(target)
        storage.end_use(target)

        self.assertEqual(storage.reap_idle(TTL), [])


if __name__ == "__main__":
    unittest.main()
