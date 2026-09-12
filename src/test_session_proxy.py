"""Browser-free tests for the proxy a session exits through.

A session's egress belongs to the session id, not to the request that happens to
name it next: clients set the proxy on sessions.create and the /v1 contract
ignores it on every later request. So every rebuild (TTL expiry, a reap, a cap
eviction, an engine fallback opening the id in the other pool) has to go back out
through the same proxy, or the browser silently solves from the server's own
address while still reporting success.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_session_proxy
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import sessions
from dtos import V1RequestBase
from engines.chrome_engine import ChromeEngine
from sessions import SessionStore

PROXY = {"url": "http://residential:8080"}


class _Builder:
    """A build callable that records the proxy each launch was given."""

    def __init__(self):
        self.proxies = []

    def __call__(self, proxy=None):
        self.proxies.append(proxy)
        return MagicMock()


def store_with(builder) -> SessionStore:
    return SessionStore(build=builder, teardown=lambda payload: None)


class SessionProxy(unittest.TestCase):

    def setUp(self):
        sessions._PROXY_BY_ID.clear()

    def test_a_session_records_the_proxy_it_was_built_with(self):
        session, _ = store_with(_Builder()).create("s", PROXY)

        self.assertEqual(session.proxy, PROXY)

    def test_an_expired_session_is_rebuilt_on_its_own_proxy(self):
        builder = _Builder()
        store = store_with(builder)
        store.create("s", PROXY)

        store.get("s", ttl=timedelta(0), proxy=None)

        self.assertEqual(builder.proxies, [PROXY, PROXY])

    def test_a_reaped_session_is_rebuilt_on_its_own_proxy(self):
        builder = _Builder()
        store = store_with(builder)
        store.create("s", PROXY)
        store.sessions["s"].last_used = datetime.now() - timedelta(hours=2)
        store.reap_idle(timedelta(minutes=30))

        store.get("s", proxy=None)

        self.assertEqual(builder.proxies, [PROXY, PROXY])

    def test_a_session_the_cap_evicted_is_rebuilt_on_its_own_proxy(self):
        builder = _Builder()
        store = store_with(builder)
        store.create("s", PROXY)
        store.sessions["s"].last_used = datetime.now() - timedelta(hours=2)
        store.create("newer")
        store.enforce_cap(1)

        store.get("s", proxy=None)

        self.assertEqual(builder.proxies[-1], PROXY)

    def test_the_other_engine_pool_builds_the_same_id_on_that_proxy(self):
        chrome_builder, stealth_builder = _Builder(), _Builder()
        store_with(chrome_builder).create("s", PROXY)

        store_with(stealth_builder).get("s", proxy=None)

        self.assertEqual(stealth_builder.proxies, [PROXY])

    def test_a_request_proxy_does_not_redirect_an_existing_session(self):
        builder = _Builder()
        store = store_with(builder)
        store.create("s", PROXY)

        store.get("s", ttl=timedelta(0), proxy={"url": "http://someone-elses:1"})

        self.assertEqual(builder.proxies, [PROXY, PROXY])

    def test_a_reaped_session_ignores_the_proxy_the_next_request_carries(self):
        builder = _Builder()
        store = store_with(builder)
        store.create("s", PROXY)
        store.sessions["s"].last_used = datetime.now() - timedelta(hours=2)
        store.reap_idle(timedelta(minutes=30))

        store.get("s", proxy={"url": "http://someone-elses:1"})

        self.assertEqual(builder.proxies, [PROXY, PROXY])

    def test_an_expired_session_rebuilds_on_its_own_proxy_once_the_memory_is_evicted(self):
        builder = _Builder()
        store = store_with(builder)
        store.create("s", PROXY)
        sessions._PROXY_BY_ID.clear()  # as the memory cap does to an old id

        store.get("s", ttl=timedelta(0), proxy={"url": "http://someone-elses:1"})

        self.assertEqual(builder.proxies, [PROXY, PROXY])

    def test_a_session_created_without_a_proxy_is_rebuilt_without_one(self):
        # "No proxy" is a choice the session was created with, so a later
        # request carrying one does not redirect it either.
        builder = _Builder()
        store = store_with(builder)
        store.create("s")
        store.sessions["s"].last_used = datetime.now() - timedelta(hours=2)
        store.reap_idle(timedelta(minutes=30))

        store.get("s", proxy={"url": "http://someone-elses:1"})

        self.assertEqual(builder.proxies, [None, None])

    def test_destroying_a_session_forgets_its_proxy(self):
        builder = _Builder()
        store = store_with(builder)
        store.create("s", PROXY)
        store.destroy("s")

        store.get("s", proxy=None)

        self.assertEqual(builder.proxies, [PROXY, None])

    def test_creating_it_again_with_another_proxy_moves_the_session(self):
        builder = _Builder()
        store = store_with(builder)
        store.create("s", PROXY)
        store.destroy("s")

        store.create("s", {"url": "http://other:1"})

        self.assertEqual(builder.proxies[-1], {"url": "http://other:1"})


class ChromeSessionTimezone(unittest.TestCase):
    """The Chrome browser's timezone has to describe the exit it really uses."""

    def setUp(self):
        sessions._PROXY_BY_ID.clear()

    def test_the_timezone_follows_the_session_proxy_not_the_request(self):
        store = store_with(_Builder())
        store.create("s", PROXY)
        engine = ChromeEngine(sessions=store)
        seen = []

        with patch("geo.browser_timezone", side_effect=lambda cfg: seen.append(cfg) or "Europe/Berlin"), \
                patch.object(ChromeEngine, "_evil_logic", return_value="solved"):
            engine.solve(V1RequestBase({"url": "https://example-site.tld/", "session": "s"}), "GET", 30)

        self.assertEqual(seen, [{"server": "http://residential:8080"}])


if __name__ == "__main__":
    unittest.main()
