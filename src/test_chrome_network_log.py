"""Browser-free tests that a Chrome session's network log does not pile up.

With RESPONSE_HEADERS on, the browser is launched logging network events, and
reading that log is what empties it. A session's browser lives across requests,
so a request that never reads it (returnOnlyCookies skips the header read, and a
failed solve never reaches it) used to leave its entries in the browser for the
life of the session.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_chrome_network_log
"""
import os
import unittest
from unittest.mock import patch

from dtos import V1RequestBase
from engines import chrome_engine
from engines.chrome_engine import ChromeEngine
from sessions import SessionStore


class _Driver:
    def __init__(self):
        self.log_reads = 0

    def get_log(self, _kind):
        self.log_reads += 1
        return []


def solve(driver, evil_logic, **fields):
    store = SessionStore(build=lambda proxy=None: driver, teardown=lambda d: None)
    store.create("s")
    req = V1RequestBase(dict({"url": "https://example-site.tld/", "session": "s"}, **fields))
    with patch.dict(os.environ, {"RESPONSE_HEADERS": "true"}), \
            patch.object(chrome_engine, "_apply_timezone", lambda *_a: None), \
            patch.object(ChromeEngine, "_evil_logic", evil_logic):
        try:
            ChromeEngine(sessions=store).solve(req, "GET", 30.0)
        except Exception:
            pass


class DrainedOnEveryRequest(unittest.TestCase):

    def test_a_request_that_reports_no_headers_still_empties_it(self):
        driver = _Driver()

        solve(driver, lambda *_a: "solved", returnOnlyCookies=True)

        self.assertEqual(driver.log_reads, 1)

    def test_a_failed_solve_still_empties_it(self):
        driver = _Driver()

        def boom(*_args):
            raise Exception("Cloudflare has blocked this request.")

        solve(driver, boom)

        self.assertEqual(driver.log_reads, 1)


if __name__ == "__main__":
    unittest.main()
