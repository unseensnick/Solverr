"""Browser-free tests for the POST form both engines navigate to.

The form is carried to the browser as a `data:text/html,` URL, so it has two
escaping jobs at once: survive the URL decode the browser does first, and survive
the HTML parse that follows. Getting either wrong is silent, and the failure only
shows up in what the target server received.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_postform
"""
import unittest

from postform import build_post_html

URL = "https://example-site.tld/login"


def field(html: str, name: str) -> str:
    """The value attribute of the named input, exactly as written."""
    marker = f'name="{name}" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def action(html: str) -> str:
    marker = 'action="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


class ActionAttribute(unittest.TestCase):

    def test_a_quote_in_the_url_cannot_close_the_attribute(self):
        html = build_post_html('https://example-site.tld/"><script>alert(1)</script><x y="', "a=1")

        self.assertNotIn("<script>alert(1)</script>", html)

    def test_an_ordinary_url_reaches_the_action_unchanged(self):
        self.assertEqual(action(build_post_html(URL, "a=1")), URL)


class FieldEncoding(unittest.TestCase):
    """A field must arrive percent-encoded, because the data: URL decodes first."""

    def test_a_percent_sign_survives_the_data_url_decode(self):
        # "100%25 off" is what a client sends for the value "100% off".
        self.assertEqual(field(build_post_html(URL, "pct=100%25%20off"), "pct"),
                         "100%25%20off")

    def test_a_hash_cannot_truncate_the_document(self):
        self.assertEqual(field(build_post_html(URL, "frag=a%23b"), "frag"), "a%23b")

    def test_an_ampersand_stays_inside_one_field(self):
        self.assertEqual(field(build_post_html(URL, "amp=x%26y"), "amp"), "x%26y")

    def test_a_plain_value_is_left_readable(self):
        self.assertEqual(field(build_post_html(URL, "plain=hello"), "plain"), "hello")


class FormShape(unittest.TestCase):

    def test_the_submit_field_is_dropped(self):
        self.assertNotIn('name="submit"', build_post_html(URL, "a=1&submit=Go"))

    def test_a_valueless_field_becomes_an_empty_one(self):
        self.assertEqual(field(build_post_html(URL, "flag"), "flag"), "")


if __name__ == "__main__":
    unittest.main()
