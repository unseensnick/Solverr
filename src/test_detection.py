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

_SAMPLES_DIR = os.path.join(os.path.dirname(__file__), '..', 'html_samples')


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


if __name__ == '__main__':
    unittest.main()
