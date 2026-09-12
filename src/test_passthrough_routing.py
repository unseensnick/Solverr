"""The passthrough request boundary: what reaches the solver and what is refused.

The byte accounting of the response cache is covered by
test_passthrough_cache.py. These cover the decisions made before a solve: which
target a path resolves to, which paths are answered without touching the solver,
the session the solve names, the lock the cache path must not hold while writing
to a client, and the interface the listener binds.
"""
import logging
import os
import threading
import types
import unittest
from unittest import mock

import passthrough

_CTYPE = passthrough._HTML_CONTENT_TYPE
_PAGE = b"<html>a page</html>"


class _Recorder(passthrough._Handler):
    """A handler with no socket: records what _send was handed.

    BaseHTTPRequestHandler.__init__ reads and answers a real request off a
    socket, so it is deliberately not called.
    """

    def __init__(self, path, command="GET"):
        self.path = path
        self.command = command
        self.sent = None
        self.lock_was_free = None

    def _send(self, status, body=b"", content_type=_CTYPE):
        # Whether the cache lock is held while the response goes out. A held
        # lock means a slow client blocks every other request's lookup.
        self.lock_was_free = not passthrough._lock.locked()
        self.sent = (status, body, content_type)

    def address_string(self):
        return "test-client"


def _solver(recorded):
    """A stand-in for _solve that records (target, host) and answers a page."""
    def solve(target, host):
        recorded.append((target, host))
        return 200, _PAGE, _CTYPE, types.SimpleNamespace(response=_PAGE.decode())
    return solve


def _run(path, command="GET"):
    """Run one request with the solver stubbed. Returns (handler, solver calls)."""
    recorded = []
    handler = _Recorder(path, command)
    with mock.patch.object(passthrough, "_solve", _solver(recorded)):
        handler._handle()
    return handler, recorded


class PassthroughRoutingTest(unittest.TestCase):

    def setUp(self):
        passthrough.reset_cache()
        passthrough._inflight.clear()
        passthrough._ALLOWED_HOSTS = {"example-site.tld"}
        passthrough._DEFAULT_HOST = "example-site.tld"
        passthrough._CACHE_TTL = 0
        passthrough._CACHE_MAX_BYTES = 0
        passthrough._TIMEOUT_MS = 90000

    def tearDown(self):
        passthrough.reset_cache()
        passthrough._inflight.clear()
        passthrough._ALLOWED_HOSTS = set()
        passthrough._DEFAULT_HOST = None

    def test_an_allow_listed_first_segment_is_solved_as_that_host(self):
        _, calls = _run("/example-site.tld/search?q=x")
        self.assertEqual(calls, [("https://example-site.tld/search?q=x", "example-site.tld")])

    def test_a_dotted_segment_that_is_not_an_allowed_host_goes_to_the_default_mirror(self):
        _, calls = _run("/download.php?id=1")
        self.assertEqual(calls, [("https://example-site.tld/download.php?id=1",
                                  "example-site.tld")])

    def test_a_dotted_segment_that_is_not_an_allowed_host_is_not_refused(self):
        handler, _ = _run("/download.php?id=1")
        self.assertEqual(handler.sent[0], 200)

    def test_a_dotless_site_internal_path_still_goes_to_the_default_mirror(self):
        _, calls = _run("/details/12345")
        self.assertEqual(calls, [("https://example-site.tld/details/12345", "example-site.tld")])

    def test_a_request_is_refused_when_no_mirror_is_configured(self):
        passthrough._ALLOWED_HOSTS = set()
        passthrough._DEFAULT_HOST = None
        handler, _ = _run("/other-site.tld/x")
        self.assertEqual(handler.sent[0], 404)

    def test_a_page_whose_query_ends_in_a_static_extension_is_solved(self):
        _, calls = _run("/example-site.tld/search?callback=a.js")
        self.assertEqual(calls, [("https://example-site.tld/search?callback=a.js",
                                  "example-site.tld")])

    def test_a_real_static_asset_path_is_answered_without_solving(self):
        handler, _ = _run("/example-site.tld/static/app.js")
        self.assertEqual(handler.sent[0], 404)

    def test_a_real_static_asset_path_never_reaches_the_solver(self):
        _, calls = _run("/example-site.tld/static/app.css?v=3")
        self.assertEqual(calls, [])


class PassthroughCacheHitTest(unittest.TestCase):

    def setUp(self):
        passthrough.reset_cache()
        passthrough._inflight.clear()
        passthrough._ALLOWED_HOSTS = {"example-site.tld"}
        passthrough._DEFAULT_HOST = "example-site.tld"
        passthrough._CACHE_TTL = 3600
        passthrough._CACHE_MAX_BYTES = 0
        passthrough._TIMEOUT_MS = 90000
        passthrough._cache_store("/example-site.tld/x", 200, _PAGE, _CTYPE)

    def tearDown(self):
        passthrough.reset_cache()
        passthrough._inflight.clear()
        passthrough._ALLOWED_HOSTS = set()
        passthrough._DEFAULT_HOST = None
        passthrough._CACHE_TTL = 0

    def test_a_cached_body_is_served_from_the_cache(self):
        handler, calls = _run("/example-site.tld/x")
        self.assertEqual((handler.sent[1], calls), (_PAGE, []))

    def test_a_cache_hit_is_written_without_holding_the_cache_lock(self):
        handler, _ = _run("/example-site.tld/x")
        self.assertTrue(handler.lock_was_free)


class PassthroughSessionTest(unittest.TestCase):

    def setUp(self):
        passthrough._TIMEOUT_MS = 90000

    def test_the_solve_names_one_session_per_upstream_host(self):
        captured = []

        def endpoint(req):
            captured.append(req)
            return types.SimpleNamespace(
                status="ok", message=None,
                solution=types.SimpleNamespace(status=200, response="<html>ok</html>",
                                               contentType=None))

        with mock.patch.object(passthrough.flaresolverr_service, "controller_v1_endpoint",
                               endpoint):
            passthrough._solve("https://example-site.tld/x", "example-site.tld")
        self.assertEqual(captured[0].session, "passthrough:example-site.tld")


class _CaptureServer:
    """Stands in for ThreadingHTTPServer: records the address, binds nothing."""
    bound = []

    def __init__(self, address, handler):
        _CaptureServer.bound.append(address)

    def serve_forever(self):
        pass


def _start_captured(**env) -> tuple:
    """Run start() with the socket and the thread stubbed. Returns the bound address."""
    _CaptureServer.bound = []
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(passthrough, "ThreadingHTTPServer", _CaptureServer), \
            mock.patch.object(threading, "Thread"):
        passthrough.start()
    return _CaptureServer.bound[0]


class PassthroughListenerTest(unittest.TestCase):

    def setUp(self):
        self._saved = (passthrough._ALLOWED_HOSTS, passthrough._DEFAULT_HOST,
                       passthrough._CACHE_TTL, passthrough._CACHE_MAX_BYTES,
                       passthrough._TIMEOUT_MS)

    def tearDown(self):
        (passthrough._ALLOWED_HOSTS, passthrough._DEFAULT_HOST, passthrough._CACHE_TTL,
         passthrough._CACHE_MAX_BYTES, passthrough._TIMEOUT_MS) = self._saved

    def test_the_listener_binds_the_interface_from_host(self):
        address = _start_captured(PASSTHROUGH_ENABLED="true", HOST="127.0.0.1")
        self.assertEqual(address[0], "127.0.0.1")

    def test_the_listener_binds_every_interface_when_host_is_unset(self):
        address = _start_captured(PASSTHROUGH_ENABLED="true")
        self.assertEqual(address[0], "0.0.0.0")

    def test_a_non_positive_timeout_falls_back_to_the_v1_default(self):
        _start_captured(PASSTHROUGH_ENABLED="true", PASSTHROUGH_TIMEOUT_MS="0")
        self.assertEqual(passthrough._TIMEOUT_MS, 60000)

    def test_a_negative_timeout_falls_back_to_the_v1_default(self):
        _start_captured(PASSTHROUGH_ENABLED="true", PASSTHROUGH_TIMEOUT_MS="-5000")
        self.assertEqual(passthrough._TIMEOUT_MS, 60000)

    def test_the_empty_allow_list_warning_names_the_status_a_request_gets(self):
        with self.assertLogs(level=logging.WARNING) as captured:
            _start_captured(PASSTHROUGH_ENABLED="true")
        self.assertIn("(404)", "\n".join(captured.output))


if __name__ == '__main__':
    unittest.main()
