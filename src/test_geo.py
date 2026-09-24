"""Browser-free tests for the shared timezone and language resolver.

The point of this module is that a solve never fails because the browser's
timezone or language could not be worked out, and that both engines are handed
the same pair from the same lookup, so these cover the precedence chains, the
cache, and every way resolution can go wrong.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_geo
"""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import config
import geo
from engines import chrome_engine

PROXY = {"url": "http://proxy.tld:8080", "username": "proxyuser", "password": "s3cr3t-pass"}
OTHER_PROXY = {"url": "http://other.tld:8080"}
# One residential endpoint, two exit countries, selected by the username.
US_EXIT = {"url": "http://proxy.tld:8080", "username": "user-country-us", "password": "s3cr3t-pass"}
DE_EXIT = {"url": "http://proxy.tld:8080", "username": "user-country-de", "password": "s3cr3t-pass"}


def _tz(proxy):
    """browser_timezone for a FlareSolverr-shaped proxy, as an engine calls it."""
    return geo.browser_timezone(geo.proxy_to_config(proxy))


def _env(**overrides):
    """os.environ with BROWSER_TIMEZONE and TZ set only as given."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('BROWSER_TIMEZONE', 'BROWSER_GEO', 'LANG', 'TZ', 'SESSION_TTL_MINUTES',
                        'PROXY_URL', 'PROXY_USERNAME', 'PROXY_PASSWORD', 'GEO_IP_LOOKUP_URLS')}
    env.update({k: v for k, v in overrides.items() if v is not None})
    return patch.dict(os.environ, env, clear=True)


class ProxyShapeTest(unittest.TestCase):

    def test_url_becomes_server(self):
        self.assertEqual(geo.proxy_to_config(PROXY)['server'], "http://proxy.tld:8080")

    def test_credentials_are_carried(self):
        self.assertEqual(geo.proxy_to_config(PROXY)['username'], "proxyuser")

    def test_no_proxy_is_none(self):
        self.assertIsNone(geo.proxy_to_config(None))

    def test_empty_proxy_is_none(self):
        self.assertIsNone(geo.proxy_to_config({}))

    # A shape that cannot be a proxy is refused rather than read as "no proxy".
    # These used to return None, so the browser launched with no proxy at all
    # and the request went out on the server's own address while reporting
    # success. `'url' not in proxy` is a substring test against a string.

    def test_proxy_without_a_url_is_refused(self):
        with self.assertRaises(Exception):
            geo.proxy_to_config({"username": "u"})

    def test_proxy_sent_as_a_string_is_refused(self):
        with self.assertRaises(Exception):
            geo.proxy_to_config("socks5://1.2.3.4:9050")

    def test_proxy_string_containing_url_is_refused(self):
        with self.assertRaises(Exception):
            geo.proxy_to_config("http://url.example.com")

    def test_blank_url_is_refused(self):
        with self.assertRaises(Exception):
            geo.proxy_to_config({"url": "   "})

    def test_the_refusal_names_the_parameter(self):
        with self.assertRaises(Exception) as caught:
            geo.proxy_to_config("socks5://1.2.3.4:9050")
        self.assertIn("proxy", str(caught.exception))


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
             patch.object(geo, '_from_egress', return_value=('Europe/Berlin', 'de-DE')):
            self.assertEqual(_tz(PROXY), 'Europe/Berlin')

    def test_pinning_both_halves_costs_no_lookup(self):
        # The language half used to resolve regardless, which is up to two
        # IP-echo round trips inside the solve budget on a host with no egress.
        with _env(BROWSER_TIMEZONE='America/Chicago', LANG='de_DE.UTF-8'), \
             patch.object(geo, '_from_egress', return_value=('Europe/Oslo', 'nb-NO')) as resolve:
            geo.browser_identity(geo.proxy_to_config(PROXY))
        resolve.assert_not_called()

    def test_a_pinned_zone_is_not_looked_up_for_the_language(self):
        asked = []
        with _env(BROWSER_TIMEZONE='America/Chicago'), \
             patch.object(geo, '_load_resolver',
                          return_value=(lambda tz, _p: asked.append(tz) or _Geo(tz, None),
                                        lambda _ip, _p: 'de-DE')):
            geo.browser_language(geo.proxy_to_config(PROXY))
        self.assertEqual(asked, ['America/Chicago'])

    def test_a_pinned_language_is_not_looked_up_for_the_zone(self):
        asked = []
        with _env(LANG='de_DE.UTF-8'), \
             patch.object(geo, '_load_resolver',
                          return_value=(lambda _tz, _p: _Geo('Europe/Berlin', None),
                                        lambda ip, _p: asked.append(ip) or 'nb-NO')):
            geo.browser_identity(geo.proxy_to_config(PROXY))
        self.assertEqual(asked, [])


class ResolvedTimezoneTest(unittest.TestCase):

    def setUp(self):
        geo.reset_cache()

    def test_unset_resolves_from_the_egress_ip(self):
        with _env(), patch.object(geo, '_from_egress', return_value=('Europe/Berlin', 'de-DE')):
            self.assertEqual(_tz(PROXY), 'Europe/Berlin')

    def test_failed_resolution_falls_back_to_the_container_timezone(self):
        with _env(TZ='Europe/Oslo'), patch.object(geo, '_from_egress', return_value=(None, None)):
            self.assertEqual(_tz(PROXY), 'Europe/Oslo')

    def test_failed_resolution_without_tz_falls_back_to_utc(self):
        with _env(), patch.object(geo, '_from_egress', return_value=(None, None)):
            self.assertEqual(_tz(PROXY), 'UTC')

    def test_a_resolved_zone_is_reused(self):
        with _env(), patch.object(geo, '_from_egress', return_value=('Europe/Berlin', 'de-DE')) as resolve:
            _tz(PROXY)
            _tz(PROXY)
        self.assertEqual(resolve.call_count, 1)

    def test_each_proxy_is_resolved_separately(self):
        with _env(), patch.object(geo, '_from_egress', return_value=('Europe/Berlin', 'de-DE')) as resolve:
            _tz(PROXY)
            _tz(OTHER_PROXY)
        self.assertEqual(resolve.call_count, 2)

    def test_exits_differing_only_by_username_are_resolved_separately(self):
        # A residential provider picks the exit country through the username, so
        # one server with two usernames is two countries, not one cached zone.
        with _env(), patch.object(geo, '_from_egress', return_value=('Europe/Berlin', 'de-DE')) as resolve:
            _tz(US_EXIT)
            _tz(DE_EXIT)
        self.assertEqual(resolve.call_count, 2)

    def test_the_cache_key_does_not_carry_the_password(self):
        self.assertNotIn('s3cr3t-pass', geo._cache_key(geo.proxy_to_config(PROXY)))


class _Clock:
    """A monotonic clock a test can advance, in place of geo's time module."""

    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


class FailedResolutionCacheTest(unittest.TestCase):
    """A bad moment must not pin the fallback pair for the whole session TTL."""

    def setUp(self):
        geo.reset_cache()

    def test_a_failure_is_not_looked_up_again_inside_its_window(self):
        with _env(), patch.object(geo, 'time', _Clock()), \
             patch.object(geo, '_from_egress', return_value=(None, None)) as resolve:
            _tz(PROXY)
            _tz(PROXY)
        self.assertEqual(resolve.call_count, 1)

    def test_a_failure_is_looked_up_again_once_its_window_passes(self):
        clock = _Clock()
        with _env(), patch.object(geo, 'time', clock), \
             patch.object(geo, '_from_egress', return_value=(None, None)) as resolve:
            _tz(PROXY)
            clock.now += geo._FAILURE_CACHE_SECONDS + 1
            _tz(PROXY)
        self.assertEqual(resolve.call_count, 2)

    def test_a_resolved_pair_outlives_that_window(self):
        clock = _Clock()
        with _env(), patch.object(geo, 'time', clock), \
             patch.object(geo, '_from_egress',
                          return_value=('Europe/Berlin', 'de-DE')) as resolve:
            _tz(PROXY)
            clock.now += geo._FAILURE_CACHE_SECONDS + 1
            _tz(PROXY)
        self.assertEqual(resolve.call_count, 1)

    def test_a_failure_never_replaces_a_resolved_pair(self):
        def resolved_meanwhile(proxy_config, *hints):
            # Another thread resolved the same exit while this lookup was out:
            # the lookups run outside the lock, so the slow failing one lands
            # last and used to overwrite the good answer.
            with patch.object(geo, '_from_egress', return_value=('Europe/Berlin', 'de-DE')):
                geo._resolved(proxy_config, *hints)
            return None, None

        with _env(), patch.object(geo, '_from_egress', side_effect=resolved_meanwhile):
            self.assertEqual(_tz(PROXY), 'Europe/Berlin')


class _Geo:
    """Stands in for invisible_core's SessionGeo."""

    def __init__(self, timezone, egress_ip):
        self.timezone = timezone
        self.egress_ip = egress_ip


def _resolver(zone='Europe/Berlin', ip='198.51.100.9', language='de-DE'):
    """A (prepare_session_geo, resolve_session_locale) pair, as the library's."""
    return (lambda _tz, _proxy: _Geo(zone, ip), lambda _ip, _proxy: language)


class ResolutionFailureTest(unittest.TestCase):
    """_from_egress must swallow everything: no browser is worse than a wrong zone."""

    def test_a_raising_zone_resolver_reports_no_zone(self):
        def boom(*_args):
            raise RuntimeError("could not discover the proxy egress IP")
        with patch.object(geo, '_load_resolver', return_value=(boom, lambda *_a: 'de-DE')), \
             self.assertLogs(level='WARNING'):
            self.assertIsNone(geo._from_egress({"server": "http://proxy.tld:8080"})[0])

    def test_a_raising_zone_resolver_still_reports_a_language(self):
        def boom(*_args):
            raise RuntimeError("nope")
        with patch.object(geo, '_load_resolver', return_value=(boom, lambda *_a: 'de-DE')), \
             self.assertLogs(level='WARNING'):
            self.assertEqual(geo._from_egress(None)[1], 'de-DE')

    def test_a_raising_language_resolver_reports_no_language(self):
        def boom(*_args):
            raise RuntimeError("nope")
        with patch.object(geo, '_load_resolver', return_value=(_resolver()[0], boom)):
            self.assertIsNone(geo._from_egress(None)[1])

    def test_an_empty_zone_reports_no_zone(self):
        with patch.object(geo, '_load_resolver', return_value=_resolver(zone='')):
            self.assertIsNone(geo._from_egress(None)[0])

    def test_a_missing_resolver_reports_neither(self):
        with patch.object(geo, '_load_resolver', return_value=None):
            self.assertEqual(geo._from_egress(None), (None, None))

    def test_the_language_reuses_the_zone_lookup_ip(self):
        seen = []
        with patch.object(geo, '_load_resolver',
                          return_value=(lambda _tz, _p: _Geo('Europe/Berlin', '198.51.100.9'),
                                        lambda ip, _p: seen.append(ip) or 'de-DE')):
            geo._from_egress(None)
        self.assertEqual(seen, ['198.51.100.9'])

    def test_the_proxy_password_is_never_logged(self):
        def boom(*_args):
            raise RuntimeError("nope")
        with patch.object(geo, '_load_resolver', return_value=(boom, lambda *_a: 'de-DE')), \
             self.assertLogs(level='WARNING') as logs:
            geo._from_egress(geo.proxy_to_config(PROXY))
        self.assertNotIn('s3cr3t-pass', logs.output[0])

    def test_a_password_inside_the_proxy_url_is_never_logged(self):
        def boom(*_args):
            raise RuntimeError("nope")
        inline = {"url": "http://proxyuser:s3cr3t-pass@proxy.tld:8080"}
        with patch.object(geo, '_load_resolver', return_value=(boom, lambda *_a: 'de-DE')),              self.assertLogs(level='WARNING') as logs:
            geo._from_egress(geo.proxy_to_config(inline))
        self.assertNotIn('s3cr3t-pass', logs.output[0])

    def test_a_percent_encoded_password_the_resolver_quotes_back_is_never_logged(self):
        # invisible_core builds its URL with quote(password, safe=''), so the
        # message carries the encoded form, not the one the config holds.
        def boom(*_args):
            raise RuntimeError("Failed to parse: "
                               "http://proxyuser:p%40ss%20w%2Frd%231@proxy.tld:8080")
        with patch.object(geo, '_load_resolver', return_value=(boom, lambda *_a: 'de-DE')),              self.assertLogs(level='WARNING') as logs:
            geo._from_egress(geo.proxy_to_config({"url": "http://proxy.tld:8080",
                                                  "username": "proxyuser",
                                                  "password": "p@ss w/rd#1"}))
        self.assertNotIn('p%40ss%20w%2Frd%231', logs.output[0])

    def test_a_password_the_resolver_quotes_back_is_never_logged(self):
        # invisible_core builds its own credentialed URL and puts it in the
        # error, so the message carries the password the config kept separate.
        def boom(*_args):
            raise RuntimeError("Failed to parse: http://proxyuser:s3cr3t-pass@proxy.tld:8080")
        with patch.object(geo, '_load_resolver', return_value=(boom, lambda *_a: 'de-DE')),              self.assertLogs(level='WARNING') as logs:
            geo._from_egress(geo.proxy_to_config(PROXY))
        self.assertNotIn('s3cr3t-pass', logs.output[0])


class BrowserLanguageTest(unittest.TestCase):

    def setUp(self):
        geo.reset_cache()

    def test_it_comes_from_the_exit_ip_when_nothing_is_set(self):
        with _env(), patch.object(geo, '_from_egress', return_value=('Europe/Oslo', 'nb-NO')):
            self.assertEqual(geo.browser_language(geo.proxy_to_config(PROXY)), 'nb-NO')

    def test_lang_wins_over_the_exit_ip(self):
        with _env(LANG='de_DE.UTF-8'), \
             patch.object(geo, '_from_egress', return_value=('Europe/Oslo', 'nb-NO')):
            self.assertEqual(geo.browser_language(geo.proxy_to_config(PROXY)), 'de-DE')

    def test_a_failed_resolution_falls_back_to_english(self):
        with _env(), patch.object(geo, '_from_egress', return_value=(None, None)):
            self.assertEqual(geo.browser_language(geo.proxy_to_config(PROXY)), 'en-US')

    def test_it_shares_one_lookup_with_the_timezone(self):
        with _env(), patch.object(geo, '_from_egress',
                                  return_value=('Europe/Oslo', 'nb-NO')) as resolve:
            geo.browser_identity(geo.proxy_to_config(PROXY))
        self.assertEqual(resolve.call_count, 1)

    def test_both_halves_come_from_the_same_country(self):
        with _env(), patch.object(geo, '_from_egress', return_value=('Europe/Oslo', 'nb-NO')):
            self.assertEqual(geo.browser_identity(geo.proxy_to_config(PROXY)),
                             ('Europe/Oslo', 'nb-NO'))


class AcceptLanguageTest(unittest.TestCase):

    def test_a_regional_tag_gains_its_base(self):
        self.assertEqual(geo.accept_language('de-DE'), 'de-DE, de')

    def test_a_bare_language_stays_alone(self):
        self.assertEqual(geo.accept_language('fr'), 'fr')


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
             patch.object(geo, '_from_egress', return_value=('Europe/Oslo', 'nb-NO')):
            self.assertEqual(_tz(PROXY), 'Europe/Oslo')

    def test_a_countryless_tag_falls_back_to_the_exit_ip(self):
        with _env(BROWSER_GEO='fr'), _with_zone_tab(), \
             patch.object(geo, '_from_egress', return_value=('Europe/Oslo', 'nb-NO')), \
             self.assertLogs(level='WARNING'):
            self.assertEqual(_tz(PROXY), 'Europe/Oslo')

    def test_a_missing_table_falls_back_to_the_exit_ip(self):
        with _env(BROWSER_GEO='de-DE'), \
             patch.object(geo, '_ZONE_TAB_PATHS', ('/nonexistent/zone1970.tab',)), \
             patch.object(geo, '_from_egress', return_value=('Europe/Oslo', 'nb-NO')), \
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
        with _env(), patch.object(geo, '_from_egress', return_value=('Europe/Berlin', 'de-DE')):
            chrome_engine._apply_timezone(driver, PROXY)
            stealth_zone = _tz(PROXY)
        self.assertEqual(driver.calls[0][1]["timezoneId"], stealth_zone)


if __name__ == '__main__':
    unittest.main()


class EnvProxyTest(unittest.TestCase):
    """One reader for PROXY_URL and its credentials.

    The startup timezone lookup used to build the dict itself and leave the
    credentials out. geo caches per proxy *server*, not per credential set, so
    an authenticated proxy refused that lookup and the fallback zone was cached
    for every request after it.
    """

    def test_no_proxy_url_means_no_proxy(self):
        with _env(PROXY_URL=None):
            self.assertIsNone(config.env_proxy())

    def test_an_empty_proxy_url_says_so_once(self):
        # It used to fail every request, which was at least loud; now the
        # traffic quietly leaves on the server's own address instead.
        config._warned_empty_proxy = False
        with _env(PROXY_URL=''), self.assertLogs(level='WARNING') as logs:
            config.env_proxy()
            config.env_proxy()
        self.assertEqual(len(logs.output), 1)

    def test_a_bare_url_needs_no_credentials(self):
        with patch.dict(os.environ, {'PROXY_URL': 'http://p:1'}, clear=True):
            self.assertEqual(config.env_proxy(), {"url": "http://p:1"})

    def test_credentials_are_carried(self):
        with patch.dict(os.environ, {'PROXY_URL': 'http://p:1', 'PROXY_USERNAME': 'u',
                                     'PROXY_PASSWORD': 'x'}, clear=True):
            self.assertEqual(config.env_proxy(),
                             {"url": "http://p:1", "username": "u", "password": "x"})

    def test_the_startup_lookup_carries_them_too(self):
        # The whole point: the cached zone must come from an authenticated lookup.
        with patch.dict(os.environ, {'PROXY_URL': 'http://p:1', 'PROXY_USERNAME': 'u',
                                     'PROXY_PASSWORD': 'x'}, clear=True):
            self.assertIn('username', geo.proxy_to_config(config.env_proxy()))


class EnvProxyInjectionTest(unittest.TestCase):
    """What the /v1 route puts on a request that carries no proxy of its own."""

    def injected(self, **env):
        import flaresolverr
        data = {"cmd": "request.get", "url": "https://example-site.tld/"}
        seen = {}

        class _Req:
            json = data

        class _Res:
            __error_500__ = False

        def handler(req):
            seen['proxy'] = req.proxy
            return _Res()

        with _env(**env), \
                patch.object(flaresolverr, 'request', _Req), \
                patch.object(flaresolverr.flaresolverr_service, 'controller_v1_endpoint',
                             handler):
            flaresolverr.controller_v1()
        return seen['proxy']

    def test_an_empty_proxy_url_means_no_proxy(self):
        self.assertIsNone(self.injected(PROXY_URL=''))

    def test_a_configured_proxy_url_is_injected(self):
        self.assertEqual(self.injected(PROXY_URL='http://proxy.tld:8080'),
                         {"url": "http://proxy.tld:8080"})

    def test_configured_credentials_are_injected_with_it(self):
        self.assertEqual(self.injected(PROXY_URL='http://proxy.tld:8080',
                                       PROXY_USERNAME='me', PROXY_PASSWORD='pw'),
                         {"url": "http://proxy.tld:8080", "username": "me", "password": "pw"})


class ResolvedCacheBoundTest(unittest.TestCase):
    """The cache key comes from a request field, so the cache is bounded."""

    def setUp(self):
        geo.reset_cache()

    def tearDown(self):
        geo.reset_cache()

    def test_a_client_sending_endless_proxies_cannot_grow_it(self):
        with patch.object(geo, '_load_resolver', return_value=_resolver()):
            for i in range(geo._MAX_CACHED_EXITS + 25):
                geo.browser_identity({"server": "http://proxy-%d.tld:8080" % i})

        self.assertEqual(len(geo._cache), geo._MAX_CACHED_EXITS)


class LookupUrlsSettingTest(unittest.TestCase):
    """GEO_IP_LOOKUP_URLS as config reads it."""

    def test_entries_are_split_and_blanks_skipped(self):
        with _env(GEO_IP_LOOKUP_URLS='https://icanhazip.com, ,https://echo.tld/ip'):
            self.assertEqual(config.geo_ip_lookup_urls(),
                             ['https://icanhazip.com', 'https://echo.tld/ip'])

    def test_an_entry_that_is_not_http_is_dropped(self):
        with _env(GEO_IP_LOOKUP_URLS='ftp://echo.tld,https://icanhazip.com'), \
             self.assertLogs(level='WARNING'):
            self.assertEqual(config.geo_ip_lookup_urls(), ['https://icanhazip.com'])

    def test_a_plain_http_entry_is_dropped(self):
        # A lookup goes through the caller's proxy, which would read it in clear.
        with _env(GEO_IP_LOOKUP_URLS='http://echo.tld/ip?token=t0k3n,https://icanhazip.com'), \
             self.assertLogs(level='WARNING'):
            self.assertEqual(config.geo_ip_lookup_urls(), ['https://icanhazip.com'])

    def test_a_dropped_entry_is_logged_without_its_password(self):
        with _env(GEO_IP_LOOKUP_URLS='ftp://me:s3cr3t-pass@echo.tld'), \
             self.assertLogs(level='WARNING') as logs:
            config.geo_ip_lookup_urls()
        self.assertNotIn('s3cr3t-pass', str(logs.output))


class ExtendLookupUrlsTest(unittest.TestCase):
    """The configured services go in front of the library's, which stay."""

    BUILTIN = ('https://api.ipify.org', 'https://icanhazip.com', 'https://checkip.amazonaws.com')

    def extended(self, library, **env):
        with _env(**env), patch.dict(sys.modules, {'invisible_core._geo': library}):
            geo.extend_lookup_urls()
        return library

    def test_configured_services_are_tried_first(self):
        library = self.extended(types.SimpleNamespace(_IP_ECHO_ENDPOINTS=self.BUILTIN),
                                GEO_IP_LOOKUP_URLS='https://echo.tld/ip')
        self.assertEqual(library._IP_ECHO_ENDPOINTS, ('https://echo.tld/ip',) + self.BUILTIN)

    def test_a_builtin_service_named_again_moves_to_the_front_once(self):
        library = self.extended(types.SimpleNamespace(_IP_ECHO_ENDPOINTS=self.BUILTIN),
                                GEO_IP_LOOKUP_URLS='https://icanhazip.com')
        self.assertEqual(library._IP_ECHO_ENDPOINTS,
                         ('https://icanhazip.com', 'https://api.ipify.org',
                          'https://checkip.amazonaws.com'))

    def test_unset_says_nothing_even_without_the_library_list(self):
        # A Chrome-only image has no stealth stack; nobody asked for anything.
        with self.assertNoLogs(level='WARNING'):
            self.extended(types.SimpleNamespace())

    def test_startup_extends_the_list_before_chrome_first_looks_up(self):
        # Launching Chrome for the user agent resolves the browser language, so
        # extending any later leaves that lookup, and its cached answer, on the
        # built-in services only.
        import flaresolverr_service
        library = types.SimpleNamespace(_IP_ECHO_ENDPOINTS=self.BUILTIN)
        seen = []

        def user_agent():
            seen.append(library._IP_ECHO_ENDPOINTS[0])
            return 'UA'

        with _env(GEO_IP_LOOKUP_URLS='https://echo.tld/ip'), \
             patch.dict(sys.modules, {'invisible_core._geo': library}), \
             patch.object(flaresolverr_service.utils, 'get_chrome_exe_path', return_value='chrome'), \
             patch.object(flaresolverr_service.utils, 'get_chrome_major_version', return_value='140'), \
             patch.object(flaresolverr_service.utils, 'get_user_agent', side_effect=user_agent), \
             patch.object(geo, 'browser_timezone', return_value='UTC'):
            flaresolverr_service.test_browser_installation()
        self.assertEqual(seen, ['https://echo.tld/ip'])

    def test_the_startup_line_names_hosts_without_their_tokens(self):
        with self.assertLogs(level='INFO') as logs:
            self.extended(types.SimpleNamespace(_IP_ECHO_ENDPOINTS=self.BUILTIN),
                          GEO_IP_LOOKUP_URLS='https://echo.tld/ip?token=t0k3n')
        self.assertNotIn('t0k3n', str(logs.output))

    def test_a_library_without_the_list_is_reported(self):
        with self.assertLogs(level='WARNING') as logs:
            self.extended(types.SimpleNamespace(), GEO_IP_LOOKUP_URLS='https://echo.tld/ip')
        self.assertIn('GEO_IP_LOOKUP_URLS has no effect', str(logs.output))
