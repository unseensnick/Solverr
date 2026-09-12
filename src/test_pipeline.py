"""The verdict rule, tested without either engine.

pipeline.verdict is a generator, so what it looks at and in what order can be
recorded directly, with no browser and no engine. The short-circuiting matters
as much as the answer: the stealth engine re-runs this on every poll while a
challenge is up, and each selector costs a round trip.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_pipeline
"""
import unittest

import budget
import pipeline
from dtos import V1RequestBase
from detection import (ACCESS_DENIED_SELECTORS, ACCESS_DENIED_TITLES,
                       CHALLENGE_SELECTORS, CHALLENGE_TITLES, TURNSTILE_SELECTORS)
from pipeline import Look, Step, Verdict


def decide(title="Example", present=(), *, turnstile_is_a_challenge=True):
    """Run the rule, returning (verdict, is_turnstile, reason, selectors looked at)."""
    looked = []

    def selector(sel):
        looked.append(sel)
        return sel in present

    found, is_turnstile, reason = pipeline.run(
        {Look.TITLE: lambda _a: title, Look.SELECTOR: selector},
        turnstile_is_a_challenge=turnstile_is_a_challenge)
    return found, is_turnstile, reason, looked


class VerdictTest(unittest.TestCase):

    def test_a_plain_page_is_nothing(self):
        self.assertEqual(decide()[0], Verdict.NONE)

    def test_every_denied_title_is_denied(self):
        # Every entry, not the first: a list the rule stops walking part way
        # through looks exactly like a list that is fully covered.
        for title in ACCESS_DENIED_TITLES:
            with self.subTest(title=title):
                self.assertEqual(decide(title=title)[0], Verdict.DENIED)

    def test_a_denied_title_matches_on_a_prefix(self):
        # startswith, not equality: upstream's list is prefixes.
        self.assertEqual(decide(title=ACCESS_DENIED_TITLES[0] + " - example")[0], Verdict.DENIED)

    def test_every_denied_selector_is_denied(self):
        for selector in ACCESS_DENIED_SELECTORS:
            with self.subTest(selector=selector):
                self.assertEqual(decide(present={selector})[0], Verdict.DENIED)

    def test_every_challenge_title_is_a_challenge(self):
        for title in CHALLENGE_TITLES:
            with self.subTest(title=title):
                self.assertEqual(decide(title=title)[0], Verdict.CHALLENGE)

    def test_a_challenge_title_matches_regardless_of_case(self):
        self.assertEqual(decide(title=CHALLENGE_TITLES[0].upper())[0], Verdict.CHALLENGE)

    def test_a_challenge_title_does_not_match_on_a_prefix(self):
        # Equality, not startswith: these are exact titles, unlike the denied list.
        self.assertEqual(decide(title=CHALLENGE_TITLES[0] + " more")[0], Verdict.NONE)

    def test_every_challenge_selector_is_a_challenge(self):
        for selector in CHALLENGE_SELECTORS:
            with self.subTest(selector=selector):
                self.assertEqual(decide(present={selector})[0], Verdict.CHALLENGE)

    def test_denied_wins_over_a_challenge(self):
        found, _t, _r, _l = decide(title=ACCESS_DENIED_TITLES[0],
                                   present={CHALLENGE_SELECTORS[0]})
        self.assertEqual(found, Verdict.DENIED)


class TurnstileCapabilityTest(unittest.TestCase):
    """The one difference between the engines is a parameter, not a fork."""

    def test_a_widget_is_a_challenge_when_the_engine_can_click_it(self):
        found, _t, _r, _l = decide(present=set(TURNSTILE_SELECTORS),
                                   turnstile_is_a_challenge=True)
        self.assertEqual(found, Verdict.CHALLENGE)

    def test_a_widget_is_not_a_challenge_when_the_engine_cannot(self):
        found, _t, _r, _l = decide(present=set(TURNSTILE_SELECTORS),
                                   turnstile_is_a_challenge=False)
        self.assertEqual(found, Verdict.NONE)

    def test_a_widget_is_reported_to_the_engine_that_can_click_it(self):
        _f, is_turnstile, _r, _l = decide(present=set(TURNSTILE_SELECTORS),
                                          turnstile_is_a_challenge=True)
        self.assertTrue(is_turnstile)

    def test_an_engine_that_cannot_click_one_is_not_asked_to_look(self):
        # Each selector is a round trip, so an answer nobody uses is not fetched.
        looked = decide(present=set(TURNSTILE_SELECTORS),
                        turnstile_is_a_challenge=False)[3]
        self.assertNotIn(TURNSTILE_SELECTORS[0], looked)


class ShortCircuitTest(unittest.TestCase):
    """Each selector is a round trip, so the rule stops as soon as it knows."""

    def test_a_denied_title_looks_at_no_selectors_at_all(self):
        self.assertEqual(decide(title=ACCESS_DENIED_TITLES[0])[3], [])

    def test_a_denied_selector_stops_at_the_one_that_matched(self):
        self.assertEqual(decide(present={ACCESS_DENIED_SELECTORS[0]})[3],
                         [ACCESS_DENIED_SELECTORS[0]])

    def test_a_clean_page_looks_at_every_selector_once_in_order(self):
        # The whole sequence: counting duplicates said nothing about a list the
        # rule never reached, which is the way coverage goes missing here.
        self.assertEqual(decide()[3], [*ACCESS_DENIED_SELECTORS, *TURNSTILE_SELECTORS,
                                       *CHALLENGE_SELECTORS])

    def test_a_challenge_title_skips_the_challenge_selectors(self):
        looked = decide(title=CHALLENGE_TITLES[0])[3]
        self.assertNotIn(CHALLENGE_SELECTORS[0], looked)


class ApproachTest(unittest.TestCase):
    """Loading the page, and the reload that supplied cookies force."""

    def steps(self, **fields):
        taken = []
        req = V1RequestBase(dict({"url": "https://example-site.tld/"}, **fields))
        pipeline.run({s: (lambda arg, s=s: taken.append(s)) for s in Step},
                     kernel=pipeline.approach, req=req)
        return taken

    def test_a_plain_request_navigates_once(self):
        self.assertEqual(self.steps(), [Step.NAVIGATE])

    def test_cookies_are_set_between_two_navigations(self):
        self.assertEqual(self.steps(cookies=[{"name": "a", "value": "1"}]),
                         [Step.NAVIGATE, Step.SET_COOKIES, Step.NAVIGATE])

    def test_an_empty_cookie_list_changes_nothing(self):
        self.assertEqual(self.steps(cookies=[]), [Step.NAVIGATE])

    def test_cookies_are_never_set_before_the_first_navigation(self):
        # They can only be set against an origin the browser is already on.
        taken = self.steps(cookies=[{"name": "a", "value": "1"}])
        self.assertEqual(taken[0], Step.NAVIGATE)


class SolveDeadlineTest(unittest.TestCase):
    """One formula, shared, rather than the same arithmetic in both engines."""

    def test_the_margin_is_kept_back_for_the_response(self):
        self.assertEqual(budget.solve_deadline(100.0, 60.0),
                         100.0 + 60.0 - budget.SOLVE_MARGIN_SECONDS)

    def test_a_budget_smaller_than_the_margin_still_allows_one_second(self):
        # A request that cannot finish is still worth one attempt; the outer cap
        # stops it either way.
        self.assertEqual(budget.solve_deadline(100.0, 1.0), 101.0)

    def test_a_zero_budget_still_allows_one_second(self):
        self.assertEqual(budget.solve_deadline(100.0, 0.0), 101.0)


if __name__ == '__main__':
    unittest.main()
