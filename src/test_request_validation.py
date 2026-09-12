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
from dtos import validate_request_types
from flaresolverr_service import _validate_max_timeout, _validate_url


class UrlSchemeTest(unittest.TestCase):

    def test_https_url_is_accepted(self):
        self.assertIsNone(_validate_url("https://example.tld/path"))

    def test_http_url_is_accepted(self):
        self.assertIsNone(_validate_url("http://example.tld/path"))

    def test_file_url_is_rejected(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'url' must be an"):
            _validate_url("file:///etc/passwd")

    def test_data_url_is_rejected(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'url' must be an"):
            _validate_url("data:text/html,<h1>hi</h1>")

    def test_url_without_a_scheme_is_rejected(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'url' must be an"):
            _validate_url("example.tld/path")

    def test_missing_url_is_rejected(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'url' is mandatory"):
            _validate_url(None)


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

        with self.assertRaisesRegex(Exception, "Request parameter 'session_ttl_minutes'"):
            flaresolverr_service._validate_session_ttl(req)

    def test_a_ttl_that_is_not_a_number_is_rejected(self):
        req = V1RequestBase({"url": "https://example-site.tld/", "session_ttl_minutes": "30"})

        with self.assertRaisesRegex(Exception, "Request parameter 'session_ttl_minutes'"):
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

        order = flaresolverr_service._engine_plan(req)

        self.assertTrue(order)


class UrlPrefix(unittest.TestCase):
    """urlparse alone let two shapes through that normalize into a fetch."""

    def test_a_leading_space_is_rejected(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'url' must be an"):
            _validate_url(" https://example-site.tld/")

    def test_a_single_slash_scheme_is_rejected(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'url' must be an"):
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
        with self.assertRaisesRegex(Exception, "Request parameter 'maxTimeout'"):
            _validate_max_timeout(self.request("soon"))

    def test_a_boolean_budget_is_refused(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'maxTimeout'"):
            _validate_max_timeout(self.request(True))

    def test_the_default_budget_respects_a_lower_ceiling(self):
        # A deployer who lowers the ceiling below the default means it for every
        # request, including the ones that ask for nothing.
        os.environ['MAX_TIMEOUT_MS'] = '30000'
        req = self.request(None)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 30000)

    def test_an_unusable_budget_falls_back_within_the_ceiling(self):
        os.environ['MAX_TIMEOUT_MS'] = '30000'
        req = self.request(0)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 30000)

    def test_a_zero_ceiling_lifts_the_clamp(self):
        os.environ['MAX_TIMEOUT_MS'] = '0'
        req = self.request(86400000)
        _validate_max_timeout(req)
        self.assertEqual(req.maxTimeout, 86400000)

class RequestTypesTest(unittest.TestCase):
    """The annotations on V1RequestBase are only binding because of this pass.

    Everything downstream reads these with `in`, truthiness, comparison or
    indexing, none of which check what they were given.
    """

    def check(self, **fields):
        validate_request_types(V1RequestBase(dict({"cmd": "request.get"}, **fields)))

    def test_a_url_that_is_not_a_string_is_refused(self):
        # Reached a regex and raised "expected string or bytes-like object".
        with self.assertRaisesRegex(Exception, "Request parameter 'url'"):
            self.check(url=12345)

    def test_a_string_in_a_boolean_is_refused(self):
        # Truthy, so "false" used to switch the feature on.
        with self.assertRaisesRegex(Exception, "Request parameter 'returnOnlyCookies'"):
            self.check(returnOnlyCookies="false")

    def test_the_refusal_names_the_parameter_and_the_type(self):
        with self.assertRaises(Exception) as caught:
            self.check(disableMedia="false")
        self.assertIn("'disableMedia' must be true or false", str(caught.exception))

    def test_cookies_that_are_not_a_list_are_refused(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'cookies'"):
            self.check(cookies="nope")

    def test_a_cookie_that_is_not_an_object_is_refused(self):
        # Both engines index a cookie by key, so this used to fail after the
        # page had been navigated to, with a Python type name for a message.
        with self.assertRaisesRegex(Exception, "Request parameter 'cookies'"):
            self.check(cookies=["name=value"])

    def test_a_proxy_that_is_not_an_object_is_refused(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'proxy'"):
            self.check(proxy="socks5://1.2.3.4:9050")

    def test_a_wait_that_is_not_a_number_is_refused(self):
        # Raised on a comparison after the challenge was already solved.
        with self.assertRaisesRegex(Exception, "Request parameter 'waitInSeconds'"):
            self.check(waitInSeconds="ten")

    def test_a_fractional_wait_is_accepted(self):
        self.assertIsNone(self.check(waitInSeconds=1.5))

    def test_a_boolean_is_not_accepted_as_a_number(self):
        with self.assertRaisesRegex(Exception, "Request parameter 'tabs_till_verify'"):
            self.check(tabs_till_verify=True)

    def test_the_deprecated_headers_object_is_accepted(self):
        # Prowlarr's form POST sends this shape; the field is never read.
        self.assertIsNone(self.check(headers={"contentType": "application/x-www-form-urlencoded"}))

    def test_a_numeric_max_timeout_string_is_left_to_its_own_validator(self):
        # Deliberately coerced rather than type-checked, for callers that work today.
        self.assertIsNone(self.check(maxTimeout="90000"))

    def test_correctly_typed_parameters_pass(self):
        self.assertIsNone(self.check(
            url="https://example-site.tld/", cookies=[{"name": "a", "value": "1"}],
            proxy={"url": "http://p:1"}, returnOnlyCookies=True, waitInSeconds=2,
            tabs_till_verify=1, engine="auto"))

    def test_an_unknown_parameter_is_kept_rather_than_refused(self):
        # The contract takes additive optional fields, so a client sending one
        # this build does not know about has to keep working.
        self.assertIsNone(self.check(url="https://example-site.tld/", someFutureField=1))


if __name__ == '__main__':
    unittest.main()
