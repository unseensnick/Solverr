"""Browser-free tests for the /v1 request boundary.

The service has no auth, so the scheme check is what stops a reachable port from
being used to read local files through the browser.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_request_validation
"""
import unittest

from flaresolverr_service import _validate_url


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
