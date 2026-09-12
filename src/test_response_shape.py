"""Browser-free tests for the shape of a solved response.

Covers the things that are easy to break silently: the /v1 payload for an
ordinary HTML solve must stay byte-identical to FlareSolverr's (no extra keys),
a non-HTML document must survive the trip through the passthrough as bytes, and
the cookie dialect translation is exact.

Rules that must hold on both engines are pinned once in test_engine_conformance,
not here. What stays is the serialization shape and the Chrome-only
tabs_till_verify path, which is a capability rather than a shared rule.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_response_shape
"""
import base64
import unittest
from unittest.mock import patch

from selenium.common import NoSuchElementException, StaleElementReferenceException

import flaresolverr_service
import passthrough
import utils
from dtos import STATUS_OK, V1RequestBase, V1ResponseBase
from engines.base import SolveResult
from engines.chrome_engine import ChromeEngine, _TURNSTILE_SELECTOR
from engines.stealth_engine import _to_client_cookies, _to_playwright_cookies

PDF_BYTES = b"%PDF-1.4 fake document"
PAGE_URL = "https://example.tld/page"

PLAYWRIGHT_COOKIE = {"name": "cf_clearance", "value": "abc", "domain": ".example.tld",
                     "path": "/", "expires": 1893456000.5, "httpOnly": True,
                     "secure": True, "sameSite": "None"}
SELENIUM_COOKIE = {"name": "cf_clearance", "value": "abc", "domain": ".example.tld",
                   "path": "/", "expiry": 1893456000, "httpOnly": True, "secure": True}


def _serialized(result: SolveResult) -> dict:
    return utils.object_to_dict(flaresolverr_service._to_challenge_resolution(result))['result']


def _solver_response(response: str, content_type: str = None) -> V1ResponseBase:
    solution = {"url": "https://example.tld/doc", "status": 200, "response": response}
    if content_type is not None:
        solution["contentType"] = content_type
    return V1ResponseBase({"status": STATUS_OK, "solution": solution})


class HtmlResponseShapeTest(unittest.TestCase):

    def test_an_html_solve_emits_only_the_fields_it_has_a_value_for(self):
        # The whole key set, not one absent key: an optional field emitted as
        # null is the shape FlareSolverr's clients do not expect, and checking
        # them one at a time leaves the next one free to appear.
        self.assertEqual(set(_serialized(SolveResult(response="<html/>"))),
                         {'url', 'status', 'cookies', 'userAgent', 'turnstile_token', 'response'})


class PdfResponseShapeTest(unittest.TestCase):

    def test_pdf_solve_reports_its_content_type(self):
        result = SolveResult(response="ZmFrZQ==", content_type="application/pdf")
        self.assertEqual(_serialized(result)['contentType'], "application/pdf")


class PassthroughBodyTest(unittest.TestCase):

    def test_pdf_solution_is_served_as_raw_bytes(self):
        encoded = base64.b64encode(PDF_BYTES).decode("ascii")
        with patch.object(flaresolverr_service, 'controller_v1_endpoint',
                          return_value=_solver_response(encoded, "application/pdf")):
            _status, body, _content_type, _solution = passthrough._solve("https://example.tld/doc", "example.tld")
        self.assertEqual(body, PDF_BYTES)

    def test_pdf_solution_is_served_under_its_content_type(self):
        encoded = base64.b64encode(PDF_BYTES).decode("ascii")
        with patch.object(flaresolverr_service, 'controller_v1_endpoint',
                          return_value=_solver_response(encoded, "application/pdf")):
            _status, _body, content_type, _solution = passthrough._solve("https://example.tld/doc", "example.tld")
        self.assertEqual(content_type, "application/pdf")

    def test_html_solution_stays_html(self):
        with patch.object(flaresolverr_service, 'controller_v1_endpoint',
                          return_value=_solver_response("<html/>")):
            _status, _body, content_type, _solution = passthrough._solve("https://example.tld/page", "example.tld")
        self.assertEqual(content_type, passthrough._HTML_CONTENT_TYPE)


class CookieShapeTest(unittest.TestCase):

    def test_returned_cookie_uses_the_selenium_expiry_key(self):
        self.assertEqual(_to_client_cookies([PLAYWRIGHT_COOKIE])[0]['expiry'], 1893456000)

    def test_returned_cookie_drops_the_playwright_expires_key(self):
        self.assertNotIn('expires', _to_client_cookies([PLAYWRIGHT_COOKIE])[0])

    def test_session_cookie_is_returned_without_an_expiry(self):
        session_cookie = dict(PLAYWRIGHT_COOKIE, expires=-1)
        self.assertNotIn('expiry', _to_client_cookies([session_cookie])[0])

    def test_client_cookie_expiry_is_translated_for_playwright(self):
        self.assertEqual(_to_playwright_cookies([SELENIUM_COOKIE], PAGE_URL)[0]['expires'],
                         1893456000.0)

    def test_client_cookie_drops_keys_playwright_rejects(self):
        self.assertNotIn('expiry', _to_playwright_cookies([SELENIUM_COOKIE], PAGE_URL)[0])

    def test_a_cookie_with_no_domain_is_anchored_to_the_page(self):
        # Playwright refuses the whole batch without a url or domain/path pair,
        # so this shape (the one the README documents) used to fail the request.
        # Selenium defaults it to the page being loaded; this matches that.
        translated = _to_playwright_cookies([{"name": "a", "value": "1"}], PAGE_URL)[0]
        self.assertEqual(translated['url'], PAGE_URL)

    def test_an_anchored_cookie_invents_no_domain(self):
        # url and domain are alternatives; sending both is what Playwright rejects.
        translated = _to_playwright_cookies([{"name": "a", "value": "1"}], PAGE_URL)[0]
        self.assertNotIn('domain', translated)

    def test_a_domain_without_a_path_gets_the_default_one(self):
        translated = _to_playwright_cookies([{"name": "a", "value": "1",
                                              "domain": ".example.tld"}], PAGE_URL)[0]
        self.assertEqual(translated['path'], '/')

    def test_a_domain_without_a_path_is_not_anchored_to_the_page(self):
        translated = _to_playwright_cookies([{"name": "a", "value": "1",
                                              "domain": ".example.tld"}], PAGE_URL)[0]
        self.assertNotIn('url', translated)

    def test_a_caller_supplied_url_is_left_alone(self):
        translated = _to_playwright_cookies([{"name": "a", "value": "1",
                                              "url": "https://other.tld/"}], PAGE_URL)[0]
        self.assertEqual(translated['url'], "https://other.tld/")

    def test_a_full_cookie_is_not_anchored(self):
        self.assertNotIn('url', _to_playwright_cookies([SELENIUM_COOKIE], PAGE_URL)[0])


_CLOCK_START = 1000.0


class _FakeClock:
    """Deterministic stand-in for the time module the Chrome engine uses."""

    def __init__(self):
        self.now = _CLOCK_START

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


# What one press costs browser-side in ActionChains pauses (pause(5) plus the
# per-tab pauses and pause(1)). A fake driver makes ActionChains raise at once,
# so without charging it here a modelled press is far cheaper than a real one,
# and a loop that overruns its deadline by a press looks like it fits.
_PRESS_SECONDS = 6.0


class _FakeSwitchTo:
    def __init__(self, driver):
        self._driver = driver

    def default_content(self):
        # click_verify always lands here, so it counts one press attempt without
        # the test having to reach inside ActionChains.
        self._driver.verify_attempts += 1
        self._driver.clock.sleep(_PRESS_SECONDS)


class _FakeElement:
    def __init__(self, value):
        self._value = value

    def get_attribute(self, _name):
        return self._value


class _FakeTurnstileDriver:
    """A page carrying a Turnstile widget, with controllable timing.

    `missing_lookups` is how many lookups happen before the input exists, which
    models a widget injected after the page finished loading. `solves_after` is
    how many verify presses fill it, or None for a widget that never solves.
    `stale_on` names lookups that raise as if the page re-rendered underneath.
    """

    def __init__(self, clock, missing_lookups=0, solves_after=1, stale_on=()):
        self.title = "Example"
        self.current_url = "https://example.tld/"
        self.page_source = "<html/>"
        self.clock = clock
        self.switch_to = _FakeSwitchTo(self)
        self.verify_attempts = 0
        self.navigations = 0
        self.lookups = 0
        self.navigations_at_first_lookup = None
        self._missing_lookups = missing_lookups
        self._solves_after = solves_after
        self._stale_on = set(stale_on)

    def get(self, _url):
        self.navigations += 1

    def delete_cookie(self, _name):
        pass

    def add_cookie(self, _cookie):
        pass

    def execute_script(self, _script):
        pass

    def get_cookies(self):
        return []

    def find_elements(self, *_args):
        return []

    def find_element(self, _by, value):
        if value != _TURNSTILE_SELECTOR:
            return object()
        self.lookups += 1
        if self.navigations_at_first_lookup is None:
            self.navigations_at_first_lookup = self.navigations
        if self.lookups in self._stale_on:
            raise StaleElementReferenceException("the page re-rendered")
        if self.lookups <= self._missing_lookups:
            raise NoSuchElementException("widget not rendered yet")
        solved = (self._solves_after is not None
                  and self.verify_attempts >= self._solves_after)
        return _FakeElement("PROBE_TOKEN" if solved else "")


class TurnstileTokenTest(unittest.TestCase):
    """The Chrome engine's tabs_till_verify path."""

    def setUp(self):
        self.clock = _FakeClock()

    def _driver(self, **kwargs):
        return _FakeTurnstileDriver(self.clock, **kwargs)

    def _solve(self, driver, timeout=30.0, **req_fields):
        fields = {"url": "https://example.tld/", "disableMedia": False,
                  "tabs_till_verify": 1}
        fields.update(req_fields)
        # The render grace is real wall-clock inside WebDriverWait, so shorten it
        # here: these cover whether a late widget is found, not how long the
        # engine is willing to wait for one.
        with patch('engines.chrome_engine._WIDGET_RENDER_SECONDS', 1.0), \
                patch('engines.chrome_engine.time', self.clock), \
                patch.object(utils, 'get_user_agent', return_value="ua"):
            return ChromeEngine(sessions=None)._evil_logic(
                V1RequestBase(fields), driver, "GET", timeout)

    def test_widget_that_renders_after_page_load_is_still_found(self):
        self.assertEqual(self._solve(self._driver(missing_lookups=1)).turnstile_token,
                         "PROBE_TOKEN")

    def test_page_without_a_widget_reports_no_token(self):
        self.assertIsNone(self._solve(self._driver(missing_lookups=99)).turnstile_token)

    def test_widget_that_never_solves_gives_up_instead_of_spinning(self):
        self.assertIsNone(
            self._solve(self._driver(solves_after=None), timeout=4.0).turnstile_token)

    def test_widget_that_never_solves_still_returns_a_page(self):
        self.assertEqual(
            self._solve(self._driver(solves_after=None), timeout=4.0).status, 200)

    def test_pressing_stops_inside_the_request_budget(self):
        # A press costs more than the margin left for building the response, so
        # a loop that checks only at the top of a pass overruns by a whole press
        # and gets killed by func_timeout instead of returning a page.
        self._solve(self._driver(solves_after=None), timeout=32.0)
        self.assertLessEqual(self.clock.now - _CLOCK_START, 32.0)

    def test_a_read_that_races_a_rerender_is_not_fatal(self):
        self.assertEqual(self._solve(self._driver(stale_on=(2,))).turnstile_token,
                         "PROBE_TOKEN")

    def test_token_is_resolved_after_the_cookie_reload(self):
        driver = self._driver()
        self._solve(driver, cookies=[{"name": "a", "value": "1"}])
        self.assertEqual(driver.navigations_at_first_lookup, 2)


if __name__ == '__main__':
    unittest.main()
