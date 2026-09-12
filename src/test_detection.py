"""Fast, browser-free tests for the shared post-solve challenge verdict.

Guards two regressions:
- a marker that also rides on solved pages (Cloudflare's post-clearance
  "/cdn-cgi/challenge-platform/" beacon) must NOT read as a challenge, or every
  request re-solves on the other engine;
- the saved real challenge pages must still read as challenges, so a genuinely
  unsolved page is never accepted as solved.
"""
import glob
import os
import unittest

import detection
from detection import CHALLENGE_HTML_MARKERS

_SAMPLES_DIR = os.path.join(os.path.dirname(__file__), '..', 'html_samples')

# A Cloudflare interstitial served in Norwegian. Every saved sample carries the
# English title, so the markers below are the only thing standing between a
# localized interstitial and being accepted as solved content.
_LOCALIZED_TITLE = 'Vent litt ...'

# One page per marker, each carrying the marker in the shape the interstitial
# writes it. Keyed by the marker so the coverage test can say which one is new.
_LOCALIZED_CHALLENGES = {
    'window._cf_chl_opt': '<script>window._cf_chl_opt={cvId:"3"};</script>',
    'cf-challenge-running': '<div id="cf-challenge-running"></div>',
    'id="challenge-form"': '<form id="challenge-form" action="/"></form>',
    'id="challenge-stage"': '<div id="challenge-stage"></div>',
    'id="challenge-error': '<div id="challenge-error-title">Error</div>',
    'turnstile-wrapper': '<div id="turnstile-wrapper"></div>',
}


def _localized_page(markup):
    return ('<html><head><title>' + _LOCALIZED_TITLE + '</title></head>'
            '<body>' + markup + '</body></html>')


def _challenge_samples():
    return sorted(glob.glob(os.path.join(_SAMPLES_DIR, 'cloudflare_*.html')))


def _read(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read()


class TestChallengeVerdict(unittest.TestCase):

    def test_saved_challenge_pages_are_all_detected(self):
        undetected = [os.path.basename(p) for p in _challenge_samples()
                      if not detection.looks_like_challenge_html(_read(p))]
        self.assertEqual(undetected, [])

    def test_samples_are_present(self):
        self.assertNotEqual(_challenge_samples(), [])

    def test_post_clearance_beacon_is_not_a_challenge(self):
        solved_page = (
            '<html><head><title>Real Page</title>'
            '<script src="/cdn-cgi/challenge-platform/h/g/scripts/x.js"></script>'
            '</head><body>content</body></html>'
        )
        self.assertFalse(detection.looks_like_challenge_html(solved_page))

    def test_plain_page_is_not_a_challenge(self):
        self.assertFalse(detection.looks_like_challenge_html(
            '<html><head><title>Home</title></head><body>hello</body></html>'))

    def test_challenge_title_is_detected(self):
        self.assertTrue(detection.looks_like_challenge_html(
            '<html><head><title>Just a moment...</title></head><body></body></html>'))

    def test_empty_html_is_not_a_challenge(self):
        self.assertFalse(detection.looks_like_challenge_html(''))

    def test_a_localized_challenge_is_detected_by_its_markup(self):
        for marker, markup in _LOCALIZED_CHALLENGES.items():
            with self.subTest(marker=marker):
                self.assertTrue(detection.looks_like_challenge_html(_localized_page(markup)))

    def test_a_localized_page_without_any_marker_is_not_a_challenge(self):
        # The title carries no weight here, so the case above is the markers'
        # doing rather than a page that would match either way.
        self.assertFalse(detection.looks_like_challenge_html(
            _localized_page('<div id="content">hello</div>')))

    def test_every_marker_has_a_page_that_exercises_it(self):
        self.assertEqual(set(_LOCALIZED_CHALLENGES), set(CHALLENGE_HTML_MARKERS))


if __name__ == '__main__':
    unittest.main()
