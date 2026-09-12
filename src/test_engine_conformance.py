"""One suite, both engines, for every rule that must hold identically.

The engines were written against each other rather than against a shared rule,
so the same defect landed in both at once: on 2026-08-25 both read the cookie
jar before `waitInSeconds` instead of after. A rule that must hold for both is
pinned once, and this is that pin for the rules no shared kernel covers yet.

Every test runs over both engines through `engine_fakes`, and reports which one
failed. Rules genuinely specific to one engine do not belong here: those are
capabilities, and they are tested where they live.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_engine_conformance
"""
import base64
import os
import unittest
from unittest.mock import patch

from detection import (ACCESS_DENIED_SELECTORS, ACCESS_DENIED_TITLES,
                       CHALLENGE_SELECTORS, CHALLENGE_TITLES, TURNSTILE_SELECTORS)
from engine_fakes import HARNESSES, World


class EngineConformanceTest(unittest.TestCase):

    def each(self, **fields):
        """Yield (engine name, result) for the same world and request on both."""
        world = World()
        for harness in HARNESSES:
            yield harness.name, harness.solve(world, **fields), world

    # ---- the cookie jar is read last ---------------------------------------

    def test_cookies_set_during_the_wait_are_returned(self):
        # The defect that motivated this suite, in both engines at once.
        for name, result, _ in self.each(waitInSeconds=2):
            with self.subTest(engine=name):
                self.assertIn("late", [c["name"] for c in result.cookies])

    def test_cookies_are_returned_when_only_cookies_were_asked_for(self):
        for name, result, _ in self.each(returnOnlyCookies=True):
            with self.subTest(engine=name):
                self.assertEqual([c["name"] for c in result.cookies], ["early"])

    # ---- returnOnlyCookies drops exactly two things ------------------------

    def test_only_cookies_drops_the_body(self):
        for name, result, _ in self.each(returnOnlyCookies=True):
            with self.subTest(engine=name):
                self.assertIsNone(result.response)

    def test_only_cookies_drops_the_headers(self):
        for name, result, _ in self.each(returnOnlyCookies=True):
            with self.subTest(engine=name):
                self.assertIsNone(result.headers)

    # ---- an ordinary solve -------------------------------------------------

    def test_an_ordinary_solve_returns_the_page(self):
        for name, result, world in self.each():
            with self.subTest(engine=name):
                self.assertEqual(result.response, world.html)

    def test_an_ordinary_solve_reports_an_empty_header_map(self):
        # Neither engine reports real headers yet, and both say so the same way.
        for name, result, _ in self.each():
            with self.subTest(engine=name):
                self.assertEqual(result.headers, {})

    def test_an_ordinary_solve_reports_200(self):
        for name, result, _ in self.each():
            with self.subTest(engine=name):
                self.assertEqual(result.status, 200)

    def test_an_unchallenged_page_says_so(self):
        # The controller and the passthrough both key on this string.
        for name, result, _ in self.each():
            with self.subTest(engine=name):
                self.assertEqual(result.message, "Challenge not detected!")

    def test_html_carries_no_content_type(self):
        # Absent for HTML keeps the payload byte-identical to FlareSolverr's.
        for name, result, _ in self.each():
            with self.subTest(engine=name):
                self.assertIsNone(result.content_type)

    def test_the_user_agent_is_reported(self):
        for name, result, world in self.each():
            with self.subTest(engine=name):
                self.assertEqual(result.user_agent, world.user_agent)

    # ---- the screenshot is opt-in ------------------------------------------

    def test_no_screenshot_unless_asked(self):
        for name, result, _ in self.each():
            with self.subTest(engine=name):
                self.assertIsNone(result.screenshot)

    def test_a_screenshot_when_asked(self):
        for name, result, world in self.each(returnScreenshot=True):
            with self.subTest(engine=name):
                self.assertEqual(base64.b64decode(result.screenshot), world.screenshot)

    # ---- one cookie dialect reaches the client -----------------------------

    def test_a_returned_cookie_uses_the_selenium_expiry_key(self):
        # Playwright says "expires" as a float; a client must not be able to tell
        # which engine solved the request.
        for name, result, _ in self.each():
            with self.subTest(engine=name):
                early = [c for c in result.cookies if c["name"] == "early"][0]
                self.assertEqual(early["expiry"], 1893456000)

    def test_a_returned_cookie_never_carries_the_playwright_key(self):
        for name, result, _ in self.each():
            with self.subTest(engine=name):
                self.assertNotIn("expires", [k for c in result.cookies for k in c])

    def test_a_session_cookie_has_no_expiry_at_all(self):
        for name, result, _ in self.each(waitInSeconds=2):
            with self.subTest(engine=name):
                late = [c for c in result.cookies if c["name"] == "late"][0]
                self.assertNotIn("expiry", late)

    def test_another_site_s_cookies_are_never_returned(self):
        # A client adds returned cookies by name with no domain check, because
        # the Chrome engine only ever reported the page's own. A session that
        # has visited two sites must not hand one site's clearance to the other.
        world = World(foreign_cookies=[("other_site_clearance", "1", None)])
        for harness in HARNESSES:
            with self.subTest(engine=harness.name):
                result = harness.solve(world)
                self.assertNotIn("other_site_clearance",
                                 [c["name"] for c in result.cookies])

    def test_both_engines_agree_on_the_cookie_key_set(self):
        seen = {}
        for name, result, _ in self.each():
            seen[name] = sorted({k for c in result.cookies for k in c})
        self.assertEqual(len(set(map(tuple, seen.values()))), 1, seen)


class DisableMediaConformanceTest(unittest.TestCase):
    """One request option, one meaning, whichever engine answers.

    It used to mean "images, CSS and fonts" on Chrome and "images, video and
    fonts" on the stealth engine, while the README promised the first on both.
    """

    def blocked(self, **fields):
        for harness in HARNESSES:
            world = World()
            harness.solve(world, **fields)
            yield harness.name, world.blocked_kinds

    def test_both_engines_block_the_same_kinds(self):
        got = dict(self.blocked(disableMedia=True))
        self.assertEqual(got["chrome"], got["stealth"])

    def test_the_kinds_are_the_ones_the_readme_promises(self):
        for name, kinds in self.blocked(disableMedia=True):
            with self.subTest(engine=name):
                self.assertEqual(kinds, {"image", "stylesheet", "font"})

    def test_a_request_that_does_not_ask_blocks_nothing(self):
        # Chrome sessions keep this setting between requests, so "nothing" has
        # to be stated rather than left to whatever the last request set.
        for name, kinds in self.blocked(disableMedia=False):
            with self.subTest(engine=name):
                self.assertEqual(kinds, set())


class ResponseHeaderConformanceTest(unittest.TestCase):
    """The first feature written once under the shared seam.

    The two engines get response headers from completely different places: the
    Chrome engine reads the browser's CDP performance log, the stealth engine
    reads the main-frame navigation response it already tracks. A client cannot
    tell which one answered, so both have to say the same thing.
    """

    HEADERS = {"content-type": "text/html", "cf-ray": "abc123"}

    def solved(self, enabled):
        env = {"RESPONSE_HEADERS": "true"} if enabled else {}
        for harness in HARNESSES:
            with patch.dict(os.environ, env, clear=True):
                yield harness.name, harness.solve(World(response_headers=dict(self.HEADERS)))

    def test_off_by_default_both_report_an_empty_map(self):
        # solution.headers has been {} since the fork; turning it on unasked
        # would change every response.
        for name, result in self.solved(enabled=False):
            with self.subTest(engine=name):
                self.assertEqual(result.headers, {})

    def test_switched_on_both_report_the_real_headers(self):
        for name, result in self.solved(enabled=True):
            with self.subTest(engine=name):
                self.assertEqual(result.headers, self.HEADERS)

    def test_both_engines_agree_exactly(self):
        got = dict(self.solved(enabled=True))
        self.assertEqual(got["chrome"].headers, got["stealth"].headers)

    def test_the_headers_describe_the_final_page(self):
        # The Chrome engine sees one entry per document, and a cleared challenge
        # leaves an earlier one behind; the last is the page the caller asked for.
        for name, result in self.solved(enabled=True):
            with self.subTest(engine=name):
                self.assertNotIn("cf-mitigated", result.headers)


class NavigationConformanceTest(unittest.TestCase):
    """Both engines load the page the same number of times, for the same reasons."""

    def navigations(self, **fields):
        for harness in HARNESSES:
            world = World()
            harness.solve(world, **fields)
            yield harness.name, len(world.navigations)

    def test_a_plain_request_loads_the_page_once(self):
        for name, count in self.navigations():
            with self.subTest(engine=name):
                self.assertEqual(count, 1)

    def test_supplied_cookies_force_a_second_load(self):
        # Cookies can only be set against an origin, so the document fetched to
        # get there was fetched without them. Skipping the reload leaves them
        # set but unused, which looks like they were ignored.
        for name, count in self.navigations(cookies=[{"name": "a", "value": "1"}]):
            with self.subTest(engine=name):
                self.assertEqual(count, 2)

    def test_a_cookie_with_no_domain_is_accepted_by_both(self):
        """The shape the README documents, and the one clients actually send.

        Playwright refuses a cookie carrying neither a url nor a domain/path
        pair, and refuses the whole batch with it, so this failed the entire
        request on the stealth engine while working on the Chrome one. Selenium
        anchors such a cookie to the page it is on; the stealth engine now does
        the same rather than handing the browser something it will reject.
        """
        for harness in HARNESSES:
            with self.subTest(engine=harness.name):
                world = World()
                harness.solve(world, cookies=[{"name": "a", "value": "1"}])
                self.assertEqual(len(world.cookies_set), 1)

    def test_a_cookie_with_a_path_and_no_domain_is_accepted_by_both(self):
        # Playwright takes a url or a domain/path pair, never a url with a path,
        # so anchoring this one to the request URL failed the whole request.
        for harness in HARNESSES:
            with self.subTest(engine=harness.name):
                world = World()
                harness.solve(world, cookies=[{"name": "a", "value": "1", "path": "/dl"}])
                self.assertEqual(len(world.cookies_set), 1)

    def test_a_cookie_with_no_path_applies_to_the_whole_site(self):
        # Selenium's default. Anchoring to the request URL scoped it to that
        # URL's directory instead, so a cookie set from /a/b was not sent to /c.
        world = World()
        HARNESSES[1].solve(world, cookies=[{"name": "a", "value": "1"}])
        self.assertEqual(world.cookies_set[0].get("path"), "/")

    def test_an_empty_cookie_list_does_not_force_one(self):
        for name, count in self.navigations(cookies=[]):
            with self.subTest(engine=name):
                self.assertEqual(count, 1)


class DetectionConformanceTest(unittest.TestCase):
    """Both engines reach the same verdict about a page from the same lists.

    The verdict rule is written once in `pipeline.py`; these pin that both
    engines still act on it, which the kernel alone cannot show.
    """

    def verdicts(self, **world):
        for harness in HARNESSES:
            try:
                yield harness.name, harness.solve(World(**world)).message
            except Exception as e:
                yield harness.name, "RAISED: " + str(e)

    def test_a_clean_page_is_not_a_challenge(self):
        for name, message in self.verdicts():
            with self.subTest(engine=name):
                self.assertEqual(message, "Challenge not detected!")

    def test_a_denied_title_is_refused(self):
        for name, message in self.verdicts(title=ACCESS_DENIED_TITLES[0], challenged_for=9):
            with self.subTest(engine=name):
                self.assertIn("Cloudflare has blocked this request", message)

    def test_a_denied_selector_is_refused(self):
        for name, message in self.verdicts(
                selectors=frozenset({ACCESS_DENIED_SELECTORS[0]}), challenged_for=9):
            with self.subTest(engine=name):
                self.assertIn("Cloudflare has blocked this request", message)

    def test_a_challenge_title_is_solved(self):
        for name, message in self.verdicts(title=CHALLENGE_TITLES[0], challenged_for=1):
            with self.subTest(engine=name):
                self.assertEqual(message, "Challenge solved!")

    def test_a_challenge_selector_is_solved(self):
        for name, message in self.verdicts(
                selectors=frozenset({CHALLENGE_SELECTORS[0]}), challenged_for=1):
            with self.subTest(engine=name):
                self.assertEqual(message, "Challenge solved!")

    def test_only_the_stealth_engine_treats_a_bare_widget_as_a_challenge(self):
        """The one declared difference, asserted rather than left to be discovered.

        A standalone Turnstile widget is a challenge to the stealth engine,
        which can click it by coordinate, and not to the Chrome engine, which
        can only reach a checkbox through a tab count the caller has to supply
        as `tabs_till_verify`. Detecting it without that count would send Chrome
        into a wait loop it cannot win, which costs the whole budget. This is a
        capability boundary, so it is asserted here; if it ever stops being one,
        this test is what says so.
        """
        got = dict(self.verdicts(selectors=frozenset(TURNSTILE_SELECTORS), challenged_for=1))
        self.assertEqual(got["stealth"], "Challenge solved!")
        self.assertEqual(got["chrome"], "Challenge not detected!")


if __name__ == '__main__':
    unittest.main()
