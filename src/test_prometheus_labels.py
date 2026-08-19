"""The metric domain label is bounded.

Prometheus keeps one time series per distinct label value for the life of the
process and nothing evicts, so labelling by request hostname made the registry
grow with the number of hosts a client asked for.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_prometheus_labels
"""
import unittest

from bottle_plugins import prometheus_plugin
from bottle_plugins.prometheus_plugin import parse_domain_url, reset_domain_labels


class DomainLabelTest(unittest.TestCase):

    def setUp(self):
        reset_domain_labels()
        self.original_cap = prometheus_plugin._MAX_DOMAIN_LABELS
        prometheus_plugin._MAX_DOMAIN_LABELS = 3

    def tearDown(self):
        prometheus_plugin._MAX_DOMAIN_LABELS = self.original_cap
        reset_domain_labels()

    def fill_to_cap(self):
        parse_domain_url("https://one.tld/a")
        parse_domain_url("https://two.tld/a")
        parse_domain_url("https://three.tld/a")

    def test_a_hostname_under_the_cap_is_reported_as_itself(self):
        self.assertEqual(parse_domain_url("https://example.tld/path"), "example.tld")

    def test_a_hostname_past_the_cap_becomes_other(self):
        self.fill_to_cap()
        self.assertEqual(parse_domain_url("https://fourth.tld/a"), "other")

    def test_a_known_hostname_still_reports_itself_once_the_cap_is_reached(self):
        self.fill_to_cap()
        parse_domain_url("https://fourth.tld/a")
        self.assertEqual(parse_domain_url("https://two.tld/b"), "two.tld")

    def test_repeating_one_hostname_does_not_consume_the_cap(self):
        parse_domain_url("https://same.tld/a")
        parse_domain_url("https://same.tld/b")
        parse_domain_url("https://same.tld/c")
        self.assertEqual(parse_domain_url("https://other-host.tld/a"), "other-host.tld")

    def test_a_url_without_a_hostname_is_unknown(self):
        self.assertEqual(parse_domain_url("not-a-url"), "unknown")

    def test_a_missing_url_is_unknown(self):
        self.assertEqual(parse_domain_url(None), "unknown")

    def test_the_other_sentinel_does_not_itself_consume_the_cap(self):
        self.fill_to_cap()
        parse_domain_url("https://fourth.tld/a")
        parse_domain_url("https://fifth.tld/a")
        self.assertEqual(parse_domain_url("https://three.tld/z"), "three.tld")

    def test_reset_clears_the_seen_set(self):
        self.fill_to_cap()
        reset_domain_labels()
        self.assertEqual(parse_domain_url("https://fourth.tld/a"), "fourth.tld")


if __name__ == '__main__':
    unittest.main()
