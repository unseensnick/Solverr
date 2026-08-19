"""Browser-free tests for the /v1 request boundary.

The service has no auth, so the scheme check is what stops a reachable port from
being used to read local files through the browser. The engine check is the other
half of the boundary: a value the service does not understand has to be refused
rather than answered on whichever engine happened to be the default.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_request_validation
"""
import os
import unittest

import flaresolverr_service
from dtos import V1RequestBase
from flaresolverr_service import _validate_max_timeout, _validate_url


class UrlSchemeTest(unittest.TestCase):

    def test_https_url_is_accepted(self):
        self.assertIsNone(_validate_url("https://example.tld/path"))

    def test_http_url_is_accepted(self):
        self.assertIsNone(_validate_url("http://example.tld/path"))

    def test_file_url_is_rejected(self):
        with self.assertRaises(Exception):
            _validate_url("file:///etc/passwd")

    def test_data_url_is_rejected(self):
        with self.assertRaises(Exception):
            _validate_url("data:text/html,<h1>hi</h1>")

    def test_url_without_a_scheme_is_rejected(self):
        with self.assertRaises(Exception):
            _validate_url("example.tld/path")

    def test_missing_url_is_rejected(self):
        with self.assertRaises(Exception):
            _validate_url(None)


if __name__ == '__main__':
    unittest.main()


class SessionTtl(unittest.TestCase):
    """A lifetime that cannot mean anything is refused, not silently applied."""

    def test_a_positive_ttl_is_accepted(self):
        req = V1RequestBase({"url": "https://example-site.tld/", "session_ttl_minutes": 30})

        self.assertIsNone(flaresolverr_service._validate_session_ttl(req))

    def test_an_unset_ttl_is_accepted(self):
        req = V1RequestBase({"url": "https://example-site.tld/"})

        self.assertIsNone(flaresolverr_service._validate_session_ttl(req))

    def test_a_negative_ttl_is_rejected(self):
        req = V1RequestBase({"url": "https://example-site.tld/", "session_ttl_minutes": -5})

        with self.assertRaises(Exception):
            flaresolverr_service._validate_session_ttl(req)

    def test_a_ttl_that_is_not_a_number_is_rejected(self):
        req = V1RequestBase({"url": "https://example-site.tld/", "session_ttl_minutes": "30"})

        with self.assertRaises(Exception):
            flaresolverr_service._validate_session_ttl(req)


class EngineSelection(unittest.TestCase):
    """An engine the service does not have is an error, not a silent default."""

    def test_a_misspelled_engine_is_rejected(self):
        req = V1RequestBase({"url": "https://example-site.tld/", "engine": "stelth"})

        with self.assertRaises(Exception) as caught:
            flaresolverr_service._engine_plan(req)

        self.assertIn("is invalid", str(caught.exception))

    def test_auto_still_means_let_the_service_choose(self):
        req = V1RequestBase({"url": "https://example-site.tld/", "engine": "auto"})

        order, _ = flaresolverr_service._engine_plan(req)

        self.assertTrue(order)


class UrlPrefix(unittest.TestCase):
    """urlparse alone let two shapes through that normalize into a fetch."""

    def test_a_leading_space_is_rejected(self):
        with self.assertRaises(Exception):
            _validate_url(" https://example-site.tld/")

    def test_a_single_slash_scheme_is_rejected(self):
        with self.assertRaises(Exception):
            _validate_url("https:/example-site.tld/")

    def test_an_uppercase_scheme_is_still_accepted(self):
        self.assertIsNone(_validate_url("HTTPS://example-site.tld/"))


class MaxTimeoutTest(unittest.TestCase):
    """maxTimeout is the whole-request budget, so an unbounded value pins a
    browser for as long as the caller asks and blocks the reaper behind it."""

    def setUp(self):
        self.original = os.environ.get('MAX_TIMEOUT_MS')
        os.environ['MAX_TIMEOUT_MS'] = '180000'

    def tearDown(self):
        if self.original is None:
            os.environ.pop('MAX_TIMEOUT_MS', None)
        else:
            os.environ['MAX_TIMEOUT_MS'] = self.original

    def request(self, value):
        req = V1RequestBase({"cmd": "request.get", "url": "https://example.tld/"})
        req.maxTimeout = value
        return req

    def test_a_missing_budget_falls_back_to_the_default(self):
        req = self.request(None)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 60000)

    def test_a_zero_budget_falls_back_to_the_default(self):
        req = self.request(0)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 60000)

    def test_a_negative_budget_falls_back_to_the_default(self):
        req = self.request(-5000)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 60000)

    def test_a_budget_under_the_ceiling_is_left_alone(self):
        req = self.request(90000)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 90000)

    def test_a_budget_at_the_ceiling_is_left_alone(self):
        req = self.request(180000)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 180000)

    def test_a_budget_over_the_ceiling_is_clamped(self):
        req = self.request(86400000)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 180000)

    def test_a_numeric_string_budget_still_works(self):
        req = self.request("90000")
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 90000)

    def test_an_unreadable_budget_is_refused(self):
        with self.assertRaises(Exception):
            _validate_max_timeout(self.request("soon"))

    def test_a_boolean_budget_is_refused(self):
        with self.assertRaises(Exception):
            _validate_max_timeout(self.request(True))

    def test_a_zero_ceiling_lifts_the_clamp(self):
        os.environ['MAX_TIMEOUT_MS'] = '0'
        req = self.request(86400000)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 86400000)
