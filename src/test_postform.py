"""Browser-free tests for the POST form both engines navigate to.

The form is carried to the browser as a `data:text/html,` URL, so it has two
escaping jobs at once: survive the URL decode the browser does first, and survive
the HTML parse that follows. Getting either wrong is silent, and the failure only
shows up in what the target server received.

Every test here therefore does what the browser does, in that order, and then
asks what the form would actually send. Reading the markup as written instead
hides a target that only breaks once it is decoded.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_postform
"""
import unittest
from html.parser import HTMLParser
from urllib.parse import unquote

from postform import build_post_html

URL = "https://example-site.tld/login"


class _Form(HTMLParser):
    """The form as the browser would have it, after decoding and parsing."""

    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.action = attrs.get("action")
        elif tag == "input":
            self.fields[attrs.get("name")] = attrs.get("value")
        elif tag == "script":
            self.scripts += 1


def submitted(url: str, post_data: str) -> _Form:
    form = _Form()
    # The browser URL-decodes the whole data: document, then parses it.
    form.feed(unquote(build_post_html(url, post_data)))
    return form


class ActionAttribute(unittest.TestCase):

    def test_an_ordinary_url_reaches_the_action_unchanged(self):
        self.assertEqual(submitted(URL, "a=1").action, URL)

    def test_a_percent_in_the_url_survives_the_data_url_decode(self):
        # An already-encoded path: the target expects the %20 to reach it.
        url = "https://example-site.tld/a%20b"
        self.assertEqual(submitted(url, "a=1").action, url)

    def test_a_hash_in_the_url_cannot_truncate_the_document(self):
        self.assertEqual(submitted(URL + "#frag", "a=1").fields, {"a": "1"})

    def test_a_quote_in_the_url_cannot_close_the_attribute(self):
        form = submitted('https://example-site.tld/"><script>alert(1)</script><x y="', "a=1")

        # One script tag, the form's own submit call.
        self.assertEqual(form.scripts, 1)


class FieldEncoding(unittest.TestCase):
    """What the server receives is the value the caller sent, byte for byte."""

    def test_a_percent_sign_arrives_as_the_caller_wrote_it(self):
        # "100%25 off" is what a client sends for the value "100% off".
        self.assertEqual(submitted(URL, "pct=100%25%20off").fields["pct"], "100% off")

    def test_a_hash_arrives_whole(self):
        self.assertEqual(submitted(URL, "frag=a%23b").fields["frag"], "a#b")

    def test_an_ampersand_stays_inside_one_field(self):
        self.assertEqual(submitted(URL, "amp=x%26y").fields["amp"], "x&y")

    def test_a_quote_in_a_value_cannot_close_the_attribute(self):
        self.assertEqual(submitted(URL, 'q=a%22%20onfocus%3Dalert(1)%20x%3D%22').fields["q"],
                         'a" onfocus=alert(1) x="')

    def test_a_quote_in_a_field_name_cannot_close_the_attribute(self):
        form = submitted(URL, 'a%22%20onfocus%3Dalert(1)%20x%3D%22=1')

        self.assertEqual(list(form.fields), ['a" onfocus=alert(1) x="'])

    def test_a_plain_value_is_left_readable(self):
        self.assertEqual(submitted(URL, "plain=hello").fields["plain"], "hello")


class FormShape(unittest.TestCase):

    def test_the_submit_field_is_dropped(self):
        self.assertNotIn("submit", submitted(URL, "a=1&submit=Go").fields)

    def test_a_valueless_field_becomes_an_empty_one(self):
        self.assertEqual(submitted(URL, "flag").fields["flag"], "")


if __name__ == "__main__":
    unittest.main()
