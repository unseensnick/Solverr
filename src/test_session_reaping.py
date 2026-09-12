"""Browser-free tests for what the reaper is allowed to close.

A session being solved on is not idle, whatever its timestamp says: quitting the
driver under a live request kills it with an "invalid session id" the caller can
do nothing about. These pin that guard for the Chrome pool, which is the one that
holds a real WebDriver.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_session_reaping
"""
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sessions import Session, SessionStore

LONG_AGO = datetime.now() - timedelta(hours=2)
TTL = timedelta(minutes=30)


def storage_with(*sessions: Session) -> SessionsStorage:
    storage = SessionStore(build=lambda proxy=None: MagicMock(), teardown=lambda d: d.quit())
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
    """Each case starts from the count its release has to change, so a mark that
    is never released shows up as a session that is never reaped."""

    def test_a_session_is_free_again_once_its_request_ends(self):
        target = session("s", LONG_AGO, in_use=1)
        storage = storage_with(target)
        storage.end_use(target)

        self.assertEqual(storage.reap_idle(TTL), ["s"])

    def test_one_of_two_requests_ending_does_not_free_the_session(self):
        target = session("s", LONG_AGO, in_use=2)
        storage = storage_with(target)
        storage.end_use(target)

        self.assertEqual(storage.reap_idle(TTL), [])

    def test_a_session_two_requests_held_is_free_once_both_end(self):
        target = session("s", LONG_AGO, in_use=2)
        storage = storage_with(target)
        storage.end_use(target)
        storage.end_use(target)

        self.assertEqual(storage.reap_idle(TTL), ["s"])


class HandingOutASession(unittest.TestCase):
    """get() marks the session, and never quits one another request is on."""

    def test_a_handed_out_session_is_marked_in_use(self):
        storage = storage_with(session("s", LONG_AGO))

        storage.get("s")

        self.assertEqual(storage.reap_idle(TTL), [])

    def test_an_expired_session_is_recreated_when_nothing_is_on_it(self):
        target = session("s", LONG_AGO)
        storage = storage_with(target)

        with patch("utils.get_webdriver", lambda proxy=None: MagicMock()):
            storage.get("s", ttl=TTL)

        self.assertTrue(target.payload.quit.called)

    def test_an_expired_session_is_reused_while_a_request_is_on_it(self):
        target = session("s", LONG_AGO, in_use=1)
        storage = storage_with(target)

        with patch("utils.get_webdriver", lambda proxy=None: MagicMock()):
            storage.get("s", ttl=TTL)

        self.assertFalse(target.payload.quit.called)


class ExpiryRace(unittest.TestCase):
    """An expired session is never replaced out from under a concurrent request.

    The check and the replacement used to be two separate lock acquisitions, so
    a second request could take the session in the gap and have its browser quit
    mid-solve, failing with an "invalid session id" it could do nothing about.
    """

    def storage(self):
        store = SessionStore(build=lambda proxy=None: MagicMock(), teardown=lambda d: d.quit())
        store.create("shared")
        return store

    def test_a_session_a_second_request_holds_is_not_replaced(self):
        store = self.storage()
        store.sessions["shared"].created_at = datetime.now() - timedelta(hours=2)
        original = store.sessions["shared"]

        # A second request is already on it when the expiry check runs.
        holder, _ = store.get("shared")
        second, _ = store.get("shared", ttl=timedelta(minutes=1))

        self.assertIs(second, original, "the browser was replaced under a live request")
        store.end_use(holder)

    def test_the_browser_a_second_request_holds_is_never_quit(self):
        store = self.storage()
        store.sessions["shared"].created_at = datetime.now() - timedelta(hours=2)
        driver = store.sessions["shared"].payload

        holder, _ = store.get("shared")
        store.get("shared", ttl=timedelta(minutes=1))

        driver.quit.assert_not_called()
        store.end_use(holder)

    def test_an_expired_session_nobody_holds_is_still_replaced(self):
        store = self.storage()
        store.sessions["shared"].created_at = datetime.now() - timedelta(hours=2)
        original = store.sessions["shared"]

        replacement, _ = store.get("shared", ttl=timedelta(minutes=1))

        self.assertIsNot(replacement, original)

    def test_the_replaced_browser_is_quit(self):
        store = self.storage()
        store.sessions["shared"].created_at = datetime.now() - timedelta(hours=2)
        driver = store.sessions["shared"].payload

        store.get("shared", ttl=timedelta(minutes=1))

        driver.quit.assert_called()


class _WindowLock:
    """A lock that runs `visitor` after one chosen release.

    Every release is a window where another request can act on the pool. Putting
    a request into each one in turn is how a race gets tested without threads
    and without relying on timing.
    """

    def __init__(self, at_release, visitor):
        self._lock = threading.Lock()
        self._at = at_release
        self._visitor = visitor
        self._releases = 0
        self._inside = False

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *_exc):
        self._lock.release()
        if self._inside:
            return False
        self._releases += 1
        if self._releases == self._at:
            self._inside = True
            try:
                self._visitor()
            finally:
                self._inside = False
        return False

    @property
    def releases(self):
        return self._releases


# A release index no release can carry, so the visitor never runs and the lock
# only counts. Releases are numbered from one.
NO_WINDOW = 0


class ExpiryRaceUnderConcurrency(unittest.TestCase):
    """No window in the expiry path hands out a session that is about to close.

    The check and the replacement used to be two separate lock acquisitions, so
    a request arriving in between took a session whose browser was then quit,
    failing mid-solve with an "invalid session id" it could do nothing about.
    """

    # Every lock release the expiry path makes: create's existing-session check,
    # get's expiry decision, and the two the rebuild's create makes around
    # launching the replacement browser outside the lock. The first case below
    # pins the count, so a new window cannot appear unwalked.
    WINDOWS = (1, 2, 3, 4)

    def expired_store(self):
        store = SessionStore(build=lambda proxy=None: MagicMock(), teardown=lambda d: d.quit())
        store.create("shared")
        store.sessions["shared"].created_at = datetime.now() - timedelta(hours=2)
        return store

    def run_with_visitor_at(self, release_index):
        """Replace an expired session while a second request arrives at one window.

        Returns the sessions that arriving request was handed, so a window that
        does not exist reports an empty list rather than a silent pass.
        """
        store = self.expired_store()
        taken = []

        def visitor():
            session, _ = store.get("shared")
            taken.append(session)

        store._lock = _WindowLock(release_index, visitor)
        store.get("shared", ttl=timedelta(minutes=1))
        return taken

    def test_the_expiry_path_opens_the_windows_these_cases_walk(self):
        store = self.expired_store()
        store._lock = _WindowLock(NO_WINDOW, lambda: None)

        store.get("shared", ttl=timedelta(minutes=1))

        self.assertEqual(store._lock.releases, len(self.WINDOWS))

    def test_no_window_hands_out_a_browser_that_is_then_quit(self):
        for window in self.WINDOWS:
            with self.subTest(window=window):
                taken = self.run_with_visitor_at(window)
                self.assertEqual([s.payload.quit.called for s in taken], [False])


class ReapRaceOnHandout(unittest.TestCase):
    """A session handed out is never one the reaper has just closed.

    A session that is found but not yet marked is idle as far as the reaper and
    the cap are concerned. Handing it back before marking it left that instant
    open, and a request could be given a browser that was already closing.
    """

    # The handout path's two lock releases: create's existing-session check and
    # get's expiry decision. Pinned by the first case below.
    WINDOWS = (1, 2)

    def store_with_an_idle_session(self):
        store = SessionStore(build=lambda proxy=None: MagicMock(),
                             teardown=lambda payload: payload.quit())
        store.create("shared")
        # Idle long enough that the reaper would take it if it were unmarked.
        store.sessions["shared"].last_used = datetime.now() - timedelta(hours=2)
        return store

    def run_with_reaper_at(self, release_index):
        """Hand out an idle session while the reaper runs at one window.

        Returns the pool, the session handed out, and what each reaper pass took,
        so a window that does not exist reports no pass at all.
        """
        store = self.store_with_an_idle_session()
        reaped = []

        def visitor():
            reaped.append(store.reap_idle(timedelta(minutes=1)))

        store._lock = _WindowLock(release_index, visitor)
        session, _fresh = store.get("shared")
        return store, session, reaped

    def test_the_handout_path_opens_the_windows_these_cases_walk(self):
        store = self.store_with_an_idle_session()
        store._lock = _WindowLock(NO_WINDOW, lambda: None)

        store.get("shared")

        self.assertEqual(store._lock.releases, len(self.WINDOWS))

    def test_no_window_lets_the_reaper_take_the_session_being_handed_out(self):
        for window in self.WINDOWS:
            with self.subTest(window=window):
                _store, _session, reaped = self.run_with_reaper_at(window)
                self.assertEqual(reaped, [[]])

    def test_the_session_handed_out_is_the_one_left_in_the_pool(self):
        for window in self.WINDOWS:
            with self.subTest(window=window):
                store, session, _reaped = self.run_with_reaper_at(window)
                self.assertIs(store.sessions.get("shared"), session)


if __name__ == "__main__":
    unittest.main()
