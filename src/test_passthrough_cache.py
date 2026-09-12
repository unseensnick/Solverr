"""The passthrough response cache stays under its byte ceiling.

The TTL bounds how long a body is kept, not how much is kept, so these cover the
size half: eviction order, the per-body ceiling, and the running total staying
honest across replaces and expiries.
"""
import time
import unittest

import passthrough


def store(path: str, size: int) -> bool:
    return passthrough._cache_store(path, 200, b"x" * size, "text/html; charset=utf-8")


def cached_paths() -> list:
    return list(passthrough._cache.keys())


class PassthroughCacheBytesTest(unittest.TestCase):

    def setUp(self):
        passthrough.reset_cache()
        passthrough._CACHE_TTL = 3600
        passthrough._CACHE_MAX_BYTES = 1000

    def tearDown(self):
        passthrough.reset_cache()
        passthrough._CACHE_TTL = 0
        passthrough._CACHE_MAX_BYTES = 0

    def test_a_stored_body_adds_its_size_to_the_total(self):
        store("/a", 100)
        self.assertEqual(passthrough._cache_bytes, 100)

    def test_a_body_within_the_ceiling_is_stored(self):
        self.assertTrue(store("/a", 250))

    def test_a_body_over_the_per_body_ceiling_is_refused(self):
        self.assertFalse(store("/big", 251))

    def test_a_refused_body_leaves_the_cache_empty(self):
        store("/big", 251)
        self.assertEqual(cached_paths(), [])

    def test_the_total_stays_under_the_cap_when_stores_exceed_it(self):
        store("/a", 200)
        store("/b", 200)
        store("/c", 200)
        store("/d", 200)
        store("/e", 200)
        store("/f", 200)
        self.assertLessEqual(passthrough._cache_bytes, 1000)

    def test_the_oldest_entry_is_evicted_first(self):
        store("/a", 200)
        store("/b", 200)
        store("/c", 200)
        store("/d", 200)
        store("/e", 200)
        store("/f", 200)
        self.assertNotIn("/a", cached_paths())

    def test_the_newest_entry_survives_eviction(self):
        store("/a", 200)
        store("/b", 200)
        store("/c", 200)
        store("/d", 200)
        store("/e", 200)
        store("/f", 200)
        self.assertIn("/f", cached_paths())

    def test_re_storing_a_path_does_not_double_count_its_bytes(self):
        store("/a", 100)
        store("/a", 100)
        self.assertEqual(passthrough._cache_bytes, 100)

    def test_re_storing_a_path_keeps_one_entry(self):
        store("/a", 100)
        store("/a", 300)
        self.assertEqual(cached_paths(), ["/a"])

    def test_an_expired_entry_is_dropped_on_the_next_store(self):
        store("/old", 100)
        expires, status, body, ctype = passthrough._cache["/old"]
        passthrough._cache["/old"] = (0, status, body, ctype)
        store("/new", 100)
        self.assertNotIn("/old", cached_paths())

    def test_an_expired_entry_releases_its_bytes(self):
        store("/old", 200)
        expires, status, body, ctype = passthrough._cache["/old"]
        passthrough._cache["/old"] = (0, status, body, ctype)
        store("/new", 100)
        self.assertEqual(passthrough._cache_bytes, 100)

    def test_a_zero_cap_lifts_the_ceiling(self):
        passthrough._CACHE_MAX_BYTES = 0
        self.assertTrue(store("/huge", 10_000))

    def test_a_zero_cap_evicts_nothing(self):
        passthrough._CACHE_MAX_BYTES = 0
        store("/a", 5_000)
        store("/b", 5_000)
        self.assertEqual(len(cached_paths()), 2)


if __name__ == '__main__':
    unittest.main()


class CacheSubstanceTest(unittest.TestCase):
    """What a body has to look like to be kept for the whole TTL.

    A site answers a transient failure with its own error page, under HTTP 200
    and with its usual layout, so nothing in the response says not to keep it.
    Keeping one for an hour is an indexer that looks broken while the site is
    fine, which is what a live run through the passthrough actually produced.
    """

    REAL = b"<html><body><a href='/torrent/123/x/'>a result</a></body></html>"
    ERROR_PAGE = b"<html><head><title>Error something went wrong.</title></head></html>"

    def setUp(self):
        passthrough.reset_cache()
        passthrough._CACHE_TTL = 3600
        passthrough._CACHE_MAX_BYTES = 0
        passthrough._CACHE_REQUIRES = "/torrent/"

    def tearDown(self):
        passthrough.reset_cache()
        passthrough._CACHE_TTL = 0
        passthrough._CACHE_REQUIRES = ""

    def test_a_body_carrying_the_marker_earns_the_full_ttl(self):
        self.assertTrue(passthrough._earns_full_ttl(self.REAL))

    def test_a_body_without_it_does_not(self):
        self.assertFalse(passthrough._earns_full_ttl(self.ERROR_PAGE))

    def test_nothing_configured_keeps_every_body(self):
        passthrough._CACHE_REQUIRES = ""
        self.assertTrue(passthrough._earns_full_ttl(self.ERROR_PAGE))

    def test_a_body_without_it_is_kept_only_briefly(self):
        self.assertEqual(passthrough._cache_ttl_for(self.ERROR_PAGE),
                         passthrough._SUSPECT_CACHE_TTL)

    def test_a_body_with_it_is_kept_for_the_configured_window(self):
        self.assertEqual(passthrough._cache_ttl_for(self.REAL), 3600)

    def test_a_shorter_configured_window_is_never_extended(self):
        passthrough._CACHE_TTL = 10
        self.assertEqual(passthrough._cache_ttl_for(self.ERROR_PAGE), 10)

    def test_a_non_html_body_keeps_the_full_window(self):
        # A PDF carries no page markup, so "no marker" says nothing about it.
        self.assertEqual(passthrough._cache_ttl_for(b"%PDF-1.4 ...", "application/pdf"), 3600)

    def test_the_short_window_reaches_the_stored_entry(self):
        passthrough._cache_store("/p", 200, self.ERROR_PAGE, "text/html",
                                 passthrough._cache_ttl_for(self.ERROR_PAGE))
        self.assertLessEqual(passthrough._cache["/p"][0] - time.monotonic(),
                             passthrough._SUSPECT_CACHE_TTL)
