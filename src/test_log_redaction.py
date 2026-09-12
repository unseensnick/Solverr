"""Browser-free tests that the /v1 request and response logs carry no credentials.

The controller logs the whole request at INFO and the whole response at DEBUG. The request holds
the proxy password (copied in from PROXY_PASSWORD whenever the client sends no proxy), cookie
values and form fields; the response holds the solved cookies and the Turnstile token. Each test
drives `controller_v1_endpoint` with the solve stubbed out and reads what reached the log.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_log_redaction
"""
import unittest
from unittest.mock import patch

import flaresolverr_service
import redact
from dtos import V1RequestBase, V1ResponseBase

SECRET = "s3cret-value"


def _logged(test, req, res):
    with patch.object(flaresolverr_service, "_controller_v1_handler", return_value=res), \
            test.assertLogs(level="DEBUG") as logs:
        flaresolverr_service.controller_v1_endpoint(req)
    return "\n".join(logs.output)


class RequestLogTest(unittest.TestCase):

    def logged(self, **fields):
        req = V1RequestBase(dict({"cmd": "request.get", "url": "https://example-site.tld/"}, **fields))
        return _logged(self, req, V1ResponseBase({}))

    def test_the_proxy_password_is_not_logged(self):
        proxy = {"url": "http://proxy.example-site.tld:8080", "username": "me", "password": SECRET}
        self.assertNotIn(SECRET, self.logged(proxy=proxy))

    def test_a_password_inside_the_proxy_url_is_not_logged(self):
        self.assertNotIn(SECRET, self.logged(proxy={"url": "http://me:%s@proxy.example-site.tld:8080" % SECRET}))

    def test_a_cookie_value_is_not_logged(self):
        self.assertNotIn(SECRET, self.logged(cookies=[{"name": "session", "value": SECRET}]))

    def test_a_form_field_value_is_not_logged(self):
        self.assertNotIn(SECRET, self.logged(cmd="request.post", postData="user=me&password=%s" % SECRET))

    def test_the_request_url_is_still_logged(self):
        self.assertIn("https://example-site.tld/", self.logged(cookies=[{"name": "session", "value": SECRET}]))

    def test_the_form_field_names_are_still_logged(self):
        self.assertIn("password=", self.logged(cmd="request.post", postData="user=me&password=%s" % SECRET))


class MalformedRequestLogTest(unittest.TestCase):
    """The request is logged before anything checks its types.

    Every field below is one the boundary is about to refuse by name, but the log
    line has already been written by then, so redaction cannot assume the shape
    it expects.
    """

    def logged(self, **fields):
        req = V1RequestBase(dict({"cmd": "request.get", "url": "https://example-site.tld/"}, **fields))
        return _logged(self, req, V1ResponseBase({}))

    def test_a_proxy_sent_as_a_string_keeps_its_password_out(self):
        self.assertNotIn(SECRET, self.logged(proxy="http://me:%s@proxy.example-site.tld:8080" % SECRET))

    def test_cookies_sent_as_an_object_keep_their_values_out(self):
        self.assertNotIn(SECRET, self.logged(cookies={"session": SECRET}))

    def test_cookies_sent_as_a_string_are_kept_out(self):
        self.assertNotIn(SECRET, self.logged(cookies="session=%s" % SECRET))

    def test_a_cookie_item_that_is_not_an_object_is_kept_out(self):
        self.assertNotIn(SECRET, self.logged(cookies=["session=%s" % SECRET]))

    def test_post_data_sent_as_an_object_keeps_its_values_out(self):
        self.assertNotIn(SECRET, self.logged(cmd="request.post", postData={"password": SECRET}))

    def test_headers_sent_as_a_list_are_kept_out(self):
        self.assertNotIn(SECRET, self.logged(headers=[{"name": "Cookie", "value": SECRET}]))

    def test_an_authorization_header_is_kept_out(self):
        self.assertNotIn(SECRET, self.logged(headers={"Authorization": "Bearer %s" % SECRET}))

    def test_an_unparseable_proxy_url_is_kept_out(self):
        # A URL urlsplit refuses outright, here an unclosed IPv6 host.
        self.assertNotIn(SECRET, self.logged(proxy={"url": "http://u:%s@[::1" % SECRET}))


class ResponseLogTest(unittest.TestCase):

    def logged(self, **solution):
        req = V1RequestBase({"cmd": "request.get", "url": "https://example-site.tld/"})
        res = V1ResponseBase({"status": "ok", "message": "",
                              "solution": dict({"url": "https://example-site.tld/", "status": 200}, **solution)})
        return _logged(self, req, res)

    def test_a_solved_cookie_value_is_not_logged(self):
        self.assertNotIn(SECRET, self.logged(cookies=[{"name": "cf_clearance", "value": SECRET}]))

    def test_a_set_cookie_header_is_not_logged(self):
        self.assertNotIn(SECRET, self.logged(headers={"Set-Cookie": "cf_clearance=%s; Path=/" % SECRET}))

    def test_the_turnstile_token_is_not_logged(self):
        self.assertNotIn(SECRET, self.logged(turnstile_token=SECRET))

    def test_the_cookie_names_are_still_logged(self):
        self.assertIn("cf_clearance", self.logged(cookies=[{"name": "cf_clearance", "value": SECRET}]))


class ProxyUrlTest(unittest.TestCase):
    """The Chrome launch logs the proxy URL it hands to --proxy-server."""

    def test_a_password_in_the_userinfo_is_replaced(self):
        self.assertNotIn(SECRET, redact.url("http://me:%s@proxy.example-site.tld:8080" % SECRET))

    def test_the_rest_of_the_url_is_kept(self):
        self.assertEqual(redact.url("http://me:%s@proxy.example-site.tld:8080" % SECRET),
                         "http://me:<redacted>@proxy.example-site.tld:8080")

    def test_a_url_without_a_password_is_untouched(self):
        self.assertEqual(redact.url("socks5://proxy.example-site.tld:1080"), "socks5://proxy.example-site.tld:1080")


if __name__ == '__main__':
    unittest.main()
