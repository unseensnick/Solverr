"""Browser-free tests for the stealth engine's challenge wait and widget click.

Covers the things a live check cannot pin down cheaply: which rect the checkbox
click is aimed at, that a clear reading is confirmed before the challenge is
called solved, and that an already-answered widget is left alone.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_stealth_click
"""
import asyncio
import unittest
from unittest.mock import patch

from playwright_captcha import CaptchaType

from detection import TURNSTILE_SELECTORS
from engines.stealth_engine import StealthEngine

TOKEN_INPUT = TURNSTILE_SELECTORS[0]

# A round is (page title, selectors present). The engine reads page.title() once
# at the top of every detection pass, so one round is consumed per pass.
CHALLENGED = ("Just a moment...", frozenset({"#challenge-form"}))
CLEARED = ("Example Domain", frozenset())
# A site's own widget: the token input with none of Cloudflare's page markup.
WIDGET = ("Sign in", frozenset({TOKEN_INPUT}))
# The same, on a site that happens to name its container the way Cloudflare's
# interstitial names one. Still a widget, not a full-page challenge.
WIDGET_IN_NAMED_WRAPPER = ("Sign in", frozenset({TOKEN_INPUT, "#turnstile-wrapper"}))
# Cloudflare's interstitial, which carries the same input inside its own page.
INTERSTITIAL = ("Just a moment...", frozenset({"#challenge-form", TOKEN_INPUT}))

CHECKBOX_ROW = {"x": 100.0, "y": 200.0, "width": 300.0, "height": 65.0}
FULL_PAGE = {"x": 0.0, "y": 0.0, "width": 1280.0, "height": 800.0}
IFRAME_RECT = {"x": 400.0, "y": 500.0, "width": 300.0, "height": 65.0}


class FakeMouse:
    def __init__(self):
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class FakeLocator:
    def __init__(self, count, box=None, value=""):
        self._count = count
        self._box = box
        self._value = value

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def bounding_box(self, timeout=None):
        return self._box

    async def input_value(self, timeout=None):
        return self._value


class FakeElement:
    def __init__(self, box):
        self._box = box

    async def bounding_box(self):
        return self._box


class FakeFrame:
    def __init__(self, url, box):
        self.url = url
        self._box = box

    async def frame_element(self):
        return FakeElement(self._box)


class FakePage:
    """Answers exactly the reads the detection and click paths make.

    ``rounds`` scripts what the page looks like over time; the last entry repeats
    once the script runs out. ``token`` is the value property of the Turnstile
    input, which is the only place a real token ever appears: the widget assigns
    it, and an assignment leaves the content attribute empty.
    """

    url = "https://example.tld/"

    def __init__(self, rounds, *, token="", container_box=None, frames=()):
        self._rounds = list(rounds)
        self._title, self._present = self._rounds[0]
        self.token = token
        self.container_box = container_box
        self.frames = list(frames)
        self.mouse = FakeMouse()

    async def title(self):
        if len(self._rounds) > 1:
            self._rounds.pop(0)
        self._title, self._present = self._rounds[0]
        return self._title

    async def query_selector(self, selector):
        return object() if selector in self._present else None

    def locator(self, selector):
        if selector == TOKEN_INPUT:
            return FakeLocator(1 if TOKEN_INPUT in self._present else 0, value=self.token)
        if TOKEN_INPUT in self._present and selector.endswith("ancestor::div[1]"):
            return FakeLocator(1, box=self.container_box)
        return FakeLocator(0)

    async def wait_for_load_state(self, state, timeout=None):
        return None


def engine() -> StealthEngine:
    """A StealthEngine with no background event loop: these coroutines run here."""
    return StealthEngine.__new__(StealthEngine)


async def wait_until_cleared(page, captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE,
                             budget=6.0) -> bool:
    deadline = asyncio.get_running_loop().time() + budget
    return await engine()._wait_until_cleared(None, page, captcha_type, deadline)


# The confirm delay and the poll interval are the clock, and waiting them out
# would make every test below take seconds for no added coverage.
fast_clock = patch.multiple("engines.stealth_engine",
                            _CHALLENGE_CONFIRM_SECONDS=0.01, _POLL_SECONDS=0.01)


class WidgetMeasurement(unittest.IsolatedAsyncioTestCase):

    async def test_the_container_is_clicked_when_no_frame_exposes_the_widget(self):
        page = FakePage([INTERSTITIAL], container_box=CHECKBOX_ROW)

        await engine()._click_turnstile(page)

        self.assertEqual(page.mouse.clicks, [(130.0, 232.5)])

    async def test_the_widget_iframe_wins_over_its_container(self):
        page = FakePage([INTERSTITIAL], container_box=CHECKBOX_ROW,
                        frames=[FakeFrame("https://challenges.cloudflare.com/x", IFRAME_RECT)])

        await engine()._click_turnstile(page)

        self.assertEqual(page.mouse.clicks, [(430.0, 532.5)])

    async def test_a_frame_with_no_url_falls_through_to_the_container(self):
        page = FakePage([INTERSTITIAL], container_box=CHECKBOX_ROW,
                        frames=[FakeFrame("", IFRAME_RECT)])

        await engine()._click_turnstile(page)

        self.assertEqual(page.mouse.clicks, [(130.0, 232.5)])

    async def test_a_page_sized_container_is_not_taken_for_a_widget(self):
        page = FakePage([INTERSTITIAL], container_box=FULL_PAGE)

        self.assertIsNone(await engine()._widget_box(page))

    async def test_nothing_is_clicked_when_the_widget_cannot_be_measured(self):
        page = FakePage([INTERSTITIAL], container_box=None)

        self.assertFalse(await engine()._click_turnstile(page))


class TokenRead(unittest.IsolatedAsyncioTestCase):

    async def test_the_token_comes_from_the_value_property(self):
        page = FakePage([WIDGET], token="cf-token-value")

        self.assertEqual(await engine()._turnstile_token(page), "cf-token-value")

    async def test_an_unanswered_widget_reports_no_token(self):
        page = FakePage([WIDGET], token="")

        self.assertIsNone(await engine()._turnstile_token(page))


class ChallengeWait(unittest.IsolatedAsyncioTestCase):

    @fast_clock
    async def test_a_challenge_that_clears_and_stays_clear_is_solved(self):
        page = FakePage([CHALLENGED, CLEARED, CLEARED])

        self.assertTrue(await wait_until_cleared(page))

    @fast_clock
    async def test_a_marker_vanishing_between_rounds_is_not_a_solved_challenge(self):
        page = FakePage([CHALLENGED, CLEARED, CHALLENGED])

        self.assertFalse(await wait_until_cleared(page, budget=0.2))

    @fast_clock
    async def test_a_challenge_that_never_clears_gives_up_at_the_deadline(self):
        page = FakePage([CHALLENGED])

        self.assertFalse(await wait_until_cleared(page, budget=0.2))

    @fast_clock
    async def test_an_answered_interstitial_widget_is_not_pressed_again(self):
        page = FakePage([INTERSTITIAL], token="already-answered",
                        container_box=CHECKBOX_ROW)

        await wait_until_cleared(page, budget=0.2)

        self.assertEqual(page.mouse.clicks, [])

    @fast_clock
    async def test_an_answered_standalone_widget_is_solved(self):
        page = FakePage([WIDGET], token="cf-token-value")

        self.assertTrue(await wait_until_cleared(page))

    @fast_clock
    async def test_a_widget_in_a_cloudflare_named_wrapper_still_counts_as_solved(self):
        page = FakePage([WIDGET_IN_NAMED_WRAPPER], token="cf-token-value")

        self.assertTrue(await wait_until_cleared(page))

    @fast_clock
    async def test_an_answered_interstitial_is_not_solved_until_its_markup_goes(self):
        page = FakePage([INTERSTITIAL], token="cf-token-value")

        self.assertFalse(await wait_until_cleared(page, budget=0.2))


if __name__ == "__main__":
    unittest.main()
