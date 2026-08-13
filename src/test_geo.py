"""Browser-free tests for the shared timezone resolver.

The point of this module is that a solve never fails because a timezone could
not be worked out, and that both engines are handed the same answer, so these
cover the precedence chain, the cache, and every way resolution can go wrong.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_geo
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import geo
from engines import chrome_engine

PROXY = {"url": "http://proxy.tld:8080", "username": "proxyuser", "password": "s3cr3t-pass"}
OTHER_PROXY = {"url": "http://other.tld:8080"}


def _tz(proxy):
    """browser_timezone for a FlareSolverr-shaped proxy, as an engine calls it."""
    return geo.browser_timezone(geo.proxy_to_config(proxy))


def _env(**overrides):
    """os.environ with BROWSER_TIMEZONE and TZ set only as given."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('BROWSER_TIMEZONE', 'BROWSER_GEO', 'LANG', 'TZ', 'SESSION_TTL_MINUTES')}
    env.update({k: v for k, v in overrides.items() if v is not None})
    return patch.dict(os.environ, env, clear=True)


class ProxyShapeTest(unittest.TestCase):

    def test_url_becomes_server(self):
        self.assertEqual(geo.proxy_to_config(PROXY)['server'], "http://proxy.tld:8080")

    def test_credentials_are_carried(self):
        self.assertEqual(geo.proxy_to_config(PROXY)['username'], "proxyuser")

    def test_no_proxy_is_none(self):
        self.assertIsNone(geo.proxy_to_config(None))

    def test_proxy_without_a_url_is_none(self):
        self.assertIsNone(geo.proxy_to_config({"username": "u"}))


class PinnedTimezoneTest(unittest.TestCase):

    def setUp(self):
        geo.reset_cache()

    def test_explicit_zone_is_used_verbatim(self):
        with _env(BROWSER_TIMEZONE='America/Chicago'):
            self.assertEqual(_tz(PROXY), 'America/Chicago')

    def test_explicit_zone_costs_no_lookup(self):
        with _env(BROWSER_TIMEZONE='America/Chicago'), \
             patch.object(geo, '_load_resolver') as resolver:
            _tz(PROXY)
        resolver.assert_not_called()

    def test_auto_falls_through_to_resolution(self):
        with _env(BROWSER_TIMEZONE='auto'), \
             patch.object(geo, '_from_egress', return_value='Europe/Berlin'):
            self.assertEqual(_tz(PROXY), 'Europe/Berlin')


class ResolvedTimezoneTest(unittest.TestCase):

    def setUp(self):
        geo.reset_cache()

    def test_unset_resolves_from_the_egress_ip(self):
        with _env(), patch.object(geo, '_from_egress', return_value='Europe/Berlin'):
            self.assertEqual(_tz(PROXY), 'Europe/Berlin')

    def test_failed_resolution_falls_back_to_the_container_timezone(self):
        with _env(TZ='Europe/Oslo'), patch.object(geo, '_from_egress', return_value=None):
            self.assertEqual(_tz(PROXY), 'Europe/Oslo')

    def test_failed_resolution_without_tz_falls_back_to_utc(self):
        with _env(), patch.object(geo, '_from_egress', return_value=None):
            self.assertEqual(_tz(PROXY), 'UTC')

    def test_a_resolved_zone_is_reused(self):
        with _env(), patch.object(geo, '_from_egress', return_value='Europe/Berlin') as resolve:
            _tz(PROXY)
            _tz(PROXY)
        self.assertEqual(resolve.call_count, 1)

    def test_each_proxy_is_resolved_separately(self):
        with _env(), patch.object(geo, '_from_egress', return_value='Europe/Berlin') as resolve:
            _tz(PROXY)
            _tz(OTHER_PROXY)
        self.assertEqual(resolve.call_count, 2)


class ResolutionFailureTest(unittest.TestCase):
    """_from_egress must swallow everything: no browser is worse than a wrong zone."""

    def test_a_raising_resolver_reports_no_zone(self):
        def boom(*_args):
            raise RuntimeError("could not discover the proxy egress IP")
        with patch.object(geo, '_load_resolver', return_value=boom), \
             self.assertLogs(level='WARNING'):
            self.assertIsNone(geo._from_egress({"server": "http://proxy.tld:8080"}))

    def test_an_empty_zone_reports_no_zone(self):
        with patch.object(geo, '_load_resolver', return_value=lambda *_a: ''):
            self.assertIsNone(geo._from_egress(None))

    def test_a_missing_resolver_reports_no_zone(self):
        with patch.object(geo, '_load_resolver', return_value=None):
            self.assertIsNone(geo._from_egress(None))

    def test_the_proxy_password_is_never_logged(self):
        def boom(*_args):
            raise RuntimeError("nope")
        with patch.object(geo, '_load_resolver', return_value=boom), \
             self.assertLogs(level='WARNING') as logs:
            geo._from_egress(geo.proxy_to_config(PROXY))
        self.assertNotIn('s3cr3t-pass', logs.output[0])


ZONE_TAB = (
    "# comment line, skipped\n"
    "CH,DE,LI\t+4723+00832\tEurope/Zurich\tSwitzerland\n"
    "DE,DK,NO,SE,SJ\t+5230+01322\tEurope/Berlin\tmost of Germany\n"
    "US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)\n"
    "US\t+415100-0873900\tAmerica/Chicago\tCentral (most areas)\n"
    "BR\t-0351-03225\tAmerica/Noronha\tAtlantic islands\n"
    "BR\t-2332-04637\tAmerica/Sao_Paulo\tBrazil (southeast)\n"
)


def _with_zone_tab(content=ZONE_TAB):
    """Point the table parser at a fixture instead of the system tzdata."""
    handle = tempfile.NamedTemporaryFile('w', suffix='.tab', delete=False, encoding='utf-8')
    handle.write(content)
    handle.close()
    return patch.object(geo, '_ZONE_TAB_PATHS', (handle.name,))


class ZoneTableTest(unittest.TestCase):

    def setUp(self):
        geo.reset_cache()

    def test_a_country_maps_to_its_zone(self):
        with _with_zone_tab():
            self.assertEqual(geo._zone_for_region('DE'), 'Europe/Berlin')

    def test_the_most_populous_zone_wins_for_a_multi_zone_country(self):
        with _with_zone_tab():
            self.assertEqual(geo._zone_for_region('US'), 'America/New_York')

    def test_a_country_sharing_a_row_maps_to_that_row(self):
        with _with_zone_tab():
            self.assertEqual(geo._zone_for_region('NO'), 'Europe/Berlin')

    def test_an_unlisted_country_has_no_zone(self):
        with _with_zone_tab():
            self.assertIsNone(geo._zone_for_region('ZZ'))

    def test_a_country_is_credited_only_to_the_row_that_leads_with_it(self):
        # Germany also appears on the Swiss row, which sorts first.
        with _with_zone_tab():
            self.assertEqual(geo._zone_for_region('DE'), 'Europe/Berlin')

    def test_a_population_override_beats_the_leading_row(self):
        with _with_zone_tab():
            self.assertEqual(geo._zone_for_region('BR'), 'America/Sao_Paulo')

    def test_an_override_naming_an_unknown_zone_is_ignored(self):
        with _with_zone_tab(), \
             patch.dict(geo._POPULATION_ZONES, {'US': 'America/Nowhere'}):
            self.assertEqual(geo._zone_for_region('US'), 'America/New_York')

    def test_a_missing_table_is_empty(self):
        with patch.object(geo, '_ZONE_TAB_PATHS', ('/nonexistent/zone1970.tab',)), \
             self.assertLogs(level='WARNING'):
            self.assertEqual(geo._zone_table(), {})


class RegionTest(unittest.TestCase):

    def test_a_region_subtag_is_the_country(self):
        self.assertEqual(geo._region_of('de-DE'), 'DE')

    def test_a_script_subtag_is_skipped(self):
        self.assertEqual(geo._region_of('zh-Hans-CN'), 'CN')

    def test_a_bare_language_has_no_country(self):
        self.assertIsNone(geo._region_of('fr'))

    def test_a_numeric_region_is_not_a_country(self):
        self.assertIsNone(geo._region_of('es-419'))


class BrowserGeoTest(unittest.TestCase):

    def setUp(self):
        geo.reset_cache()

    def test_it_sets_the_timezone(self):
        with _env(BROWSER_GEO='de-DE'), _with_zone_tab():
            self.assertEqual(_tz(PROXY), 'Europe/Berlin')

    def test_it_costs_no_egress_lookup(self):
        with _env(BROWSER_GEO='de-DE'), _with_zone_tab(), \
             patch.object(geo, '_from_egress') as resolve:
            _tz(PROXY)
        resolve.assert_not_called()

    def test_an_explicit_timezone_wins_over_it(self):
        with _env(BROWSER_GEO='de-DE', BROWSER_TIMEZONE='America/Chicago'), _with_zone_tab():
            self.assertEqual(_tz(PROXY), 'America/Chicago')

    def test_an_explicit_auto_asks_for_the_exit_ip_instead(self):
        with _env(BROWSER_GEO='de-DE', BROWSER_TIMEZONE='auto'), _with_zone_tab(), \
             patch.object(geo, '_from_egress', return_value='Europe/Oslo'):
            self.assertEqual(_tz(PROXY), 'Europe/Oslo')

    def test_a_countryless_tag_falls_back_to_the_exit_ip(self):
        with _env(BROWSER_GEO='fr'), _with_zone_tab(), \
             patch.object(geo, '_from_egress', return_value='Europe/Oslo'), \
             self.assertLogs(level='WARNING'):
            self.assertEqual(_tz(PROXY), 'Europe/Oslo')

    def test_a_missing_table_falls_back_to_the_exit_ip(self):
        with _env(BROWSER_GEO='de-DE'), \
             patch.object(geo, '_ZONE_TAB_PATHS', ('/nonexistent/zone1970.tab',)), \
             patch.object(geo, '_from_egress', return_value='Europe/Oslo'), \
             self.assertLogs(level='WARNING'):
            self.assertEqual(_tz(PROXY), 'Europe/Oslo')


class _Driver:
    """Records CDP calls, or refuses them when `fails` is set."""

    def __init__(self, fails=False):
        self.calls = []
        self.fails = fails

    def execute_cdp_cmd(self, name, params):
        if self.fails:
            raise RuntimeError("CDP unavailable")
        self.calls.append((name, params))


class ChromeTimezoneTest(unittest.TestCase):

    def setUp(self):
        geo.reset_cache()

    def test_the_resolved_zone_is_pushed_to_chrome(self):
        driver = _Driver()
        with _env(BROWSER_TIMEZONE='Europe/Berlin'):
            chrome_engine._apply_timezone(driver, PROXY)
        self.assertEqual(driver.calls,
                         [("Emulation.setTimezoneOverride", {"timezoneId": "Europe/Berlin"})])

    def test_a_driver_without_cdp_does_not_fail_the_request(self):
        with _env(BROWSER_TIMEZONE='Europe/Berlin'):
            self.assertIsNone(chrome_engine._apply_timezone(_Driver(fails=True), PROXY))

    def test_both_engines_are_given_the_same_zone(self):
        driver = _Driver()
        with _env(), patch.object(geo, '_from_egress', return_value='Europe/Berlin'):
            chrome_engine._apply_timezone(driver, PROXY)
            stealth_zone = _tz(PROXY)
        self.assertEqual(driver.calls[0][1]["timezoneId"], stealth_zone)


if __name__ == '__main__':
    unittest.main()
