"""Browser timezone resolution, shared by both engines.

The stealth stack resolves a timezone from the egress IP itself, on every
browser launch, inside the library where the answer cannot be reused. That
costs an IP-echo round trip under a 15 second budget per launch, and behind a
proxy a discovery failure raises instead of degrading, which kills the launch
outright (`prepare_session_geo` in `invisible_core/_geo.py`).

Solverr resolves it here instead: once per proxy, cached, never fatal, and the
same answer goes to both engines so they cannot disagree about where the
browser is. Handing the library a concrete zone also returns it before its own
fatal branch, so a solve can no longer fail because an IP-echo endpoint was
unreachable.
"""
import logging
import os
import threading
import time
from typing import Optional

import config

# A resolved zone outlives a browser: a session keeps its launch-time timezone
# for as long as it lives, so caching for the session TTL is no staler than
# what the engines already do. The floor keeps SESSION_TTL_MINUTES=0 (reaping
# disabled) from meaning "resolve on every launch".
_MIN_CACHE_SECONDS = 300

# zone1970.tab is the maintained one; zone.tab is its single-country-per-row
# predecessor, kept as a fallback for systems that still ship only that.
_ZONE_TAB_PATHS = ("/usr/share/zoneinfo/zone1970.tab", "/usr/share/zoneinfo/zone.tab")

_lock = threading.Lock()
_cache = {}  # proxy server -> (expires_monotonic, zone)
_geo_zones = {}  # BROWSER_GEO tag -> zone or None
_zone_table_cache = None
_known_zones_cache = None


def proxy_to_config(proxy: Optional[dict]) -> Optional[dict]:
    """A FlareSolverr proxy dict ({url, username, password}) as the Playwright,
    Camoufox and invisible_core shape ({server, username, password})."""
    if not proxy or 'url' not in proxy:
        return None
    cfg = {"server": proxy['url']}
    if proxy.get('username'):
        cfg['username'] = proxy['username']
    if proxy.get('password'):
        cfg['password'] = proxy['password']
    return cfg


def browser_timezone(proxy_config: Optional[dict] = None) -> str:
    """The IANA zone both engines should run in. Never raises.

    Takes the {server, ...} shape from ``proxy_to_config``, which is what the
    stealth engine already holds; the Chrome engine converts at the call site.

    An explicit BROWSER_TIMEZONE wins and costs nothing. Otherwise the zone
    comes from the egress IP, cached per proxy, falling back to the container's
    own timezone when that cannot be reached.

    Can block for as long as the egress lookup takes, so call it off the stealth
    event loop.
    """
    configured = config.browser_timezone()
    if configured and configured.lower() != 'auto':
        return configured
    if not configured:
        # An explicit BROWSER_TIMEZONE=auto asks for the exit IP, so it overrides
        # BROWSER_GEO. Only an unset one lets BROWSER_GEO supply the zone.
        from_geo = _zone_from_geo()
        if from_geo:
            return from_geo

    key = (proxy_config or {}).get("server") or ""

    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

    zone = _from_egress(proxy_config) or container_timezone()

    with _lock:
        _cache[key] = (time.monotonic() + _cache_seconds(), zone)
    return zone


def _zone_from_geo() -> Optional[str]:
    """The zone BROWSER_GEO implies, or None to fall through to the exit IP."""
    tag = config.browser_geo()
    if not tag:
        return None
    with _lock:
        if tag in _geo_zones:
            return _geo_zones[tag]

    region = _region_of(tag)
    zone = _zone_for_region(region) if region else None
    if zone:
        # Announced, not silent: for a country with several zones this is the
        # most populous one rather than a fact, and the reader needs to know
        # which knob corrects it.
        logging.info("BROWSER_GEO=%s puts the browser in %s; set BROWSER_TIMEZONE to override",
                     tag, zone)
    elif region:
        logging.warning("BROWSER_GEO=%s names no country this system has a timezone for; "
                        "deriving the timezone from the exit IP instead", tag)
    else:
        logging.warning("BROWSER_GEO=%s has no country in it, so it cannot set a timezone; "
                        "deriving it from the exit IP instead. Use a tag like 'de-DE'.", tag)
    with _lock:
        _geo_zones[tag] = zone
    return zone


def _region_of(tag: str) -> Optional[str]:
    """The ISO 3166 country in a language tag, or None if it carries none.

    A numeric region ('es-419' for Latin America) is deliberately not one: it
    names a continent-sized area with many zones, so there is nothing to pick.
    """
    parts = tag.split('-')
    if len(parts) < 2:
        return None
    last = parts[-1]
    return last.upper() if len(last) == 2 and last.isalpha() else None


# tzdata orders a country's zones east to west, so a country wide enough to have
# several leads with its eastern edge rather than with its population. That is
# harmless for twenty of the twenty-four multi-zone countries (the US rows lead
# with America/New_York, Mexico with America/Mexico_City), and wrong for these
# four, where the leading zone is a small island or a far-western city on a
# different UTC offset from where nearly everyone lives. Measured against
# zone1970.tab on 2026-08-13; BROWSER_TIMEZONE overrides all of it.
_POPULATION_ZONES = {
    "AU": "Australia/Sydney",   # leads with Lord Howe Island, population ~400
    "BR": "America/Sao_Paulo",  # leads with Fernando de Noronha, population ~3000
    "CA": "America/Toronto",    # leads with St John's, Newfoundland
    "RU": "Europe/Moscow",      # leads with Kaliningrad, the western exclave
}


def _zone_for_region(region: str) -> Optional[str]:
    """A country's principal timezone, from the system's own tzdata table.

    An override only applies when the system agrees the zone exists, so a typo
    here degrades to tzdata's own pick rather than to a zone no browser knows.
    """
    table = _zone_table()
    override = _POPULATION_ZONES.get(region)
    if override and override in _known_zones():
        return override
    return table.get(region)


def _zone_table() -> dict:
    """Country to timezone, parsed once from zone1970.tab.

    Read from tzdata rather than kept as a table here, because a hand-written
    country list is how upstream Camoufox ended up returning wrong zones.
    Column 1 is a comma-separated country list, column 3 the zone name.

    The header promises rows are ordered to put "the most populous timezones
    first, where that does not contradict" geographical sense. Measured, the
    geography half wins for the four countries in _POPULATION_ZONES, so the
    first row is the right pick everywhere except there.
    """
    global _zone_table_cache, _known_zones_cache
    if _zone_table_cache is not None:
        return _zone_table_cache

    table = {}
    for path in _ZONE_TAB_PATHS:
        own, shared = {}, {}
        try:
            with open(path, encoding='utf-8') as handle:
                for line in handle:
                    if line.startswith('#'):
                        continue
                    columns = line.rstrip('\n').split('\t')
                    if len(columns) < 3:
                        continue
                    countries = [c.strip().upper() for c in columns[0].split(',') if c.strip()]
                    if not countries:
                        continue
                    zone = columns[2].strip()
                    # The row's FIRST country is the one it speaks for: the file
                    # header says a shared zone is named after its most populous
                    # city and that country is listed first. Crediting every
                    # country on the row instead put Germany in Europe/Zurich,
                    # because the CH row sorts ahead of the DE one.
                    own.setdefault(countries[0], zone)
                    for code in countries:
                        shared.setdefault(code, zone)
        except OSError:
            continue
        if own or shared:
            table = {**shared, **own}
            break

    if not table:
        logging.warning("no timezone table at %s, so BROWSER_GEO cannot set a timezone",
                        " or ".join(_ZONE_TAB_PATHS))
    _zone_table_cache = table
    return table


def _known_zones() -> set:
    """Every zone name the system's table lists, for validating an override."""
    global _known_zones_cache
    if _known_zones_cache is None:
        _known_zones_cache = set(_zone_table().values()) | _all_zone_names()
    return _known_zones_cache


def _all_zone_names() -> set:
    """Zone names from every row, not just the one picked per country.

    A population override names a zone that is rarely any country's first row,
    so the per-country table alone would reject all four of them.
    """
    names = set()
    for path in _ZONE_TAB_PATHS:
        try:
            with open(path, encoding='utf-8') as handle:
                for line in handle:
                    if line.startswith('#'):
                        continue
                    columns = line.rstrip('\n').split('\t')
                    if len(columns) >= 3:
                        names.add(columns[2].strip())
        except OSError:
            continue
        if names:
            break
    return names


def container_timezone() -> str:
    """The container's own timezone, which is what a browser gets when nothing
    resolves. FlareSolverr documents TZ as the browser's timezone too, so this
    is the behavior a deployer already expects when there is nothing to derive
    a zone from."""
    return (os.environ.get('TZ', '') or '').strip() or 'UTC'


def _cache_seconds() -> int:
    return max(_MIN_CACHE_SECONDS, config.session_ttl_minutes() * 60)


def _load_resolver():
    """The stealth stack's egress-to-zone resolver, or None if unavailable.

    The only boundary this module has to the outside world, kept in one place so
    a Chrome-only runtime missing the stealth stack degrades instead of failing.
    """
    try:
        from invisible_core import resolve_session_timezone
        return resolve_session_timezone
    except Exception:
        logging.debug("geo resolution unavailable, using the container timezone", exc_info=True)
        return None


def _from_egress(proxy_config: Optional[dict]) -> Optional[str]:
    """The zone for the egress IP, or None if it cannot be determined.

    Swallows everything on purpose: this is the difference between a browser
    that launches with a slightly wrong timezone and no browser at all. The
    library raises here behind a proxy, and a proxy endpoint without a port
    raises before any request is made.
    """
    resolver = _load_resolver()
    if resolver is None:
        return None
    try:
        return resolver("auto", proxy_config) or None
    except Exception as e:
        # Server only, never the dict: it carries the proxy password.
        logging.warning("could not resolve a timezone for %s (%s); using %s",
                        (proxy_config or {}).get("server") or "the direct connection",
                        e, container_timezone())
        return None


def reset_cache() -> None:
    """Drop resolved zones and the parsed timezone table. For tests."""
    global _zone_table_cache, _known_zones_cache
    with _lock:
        _cache.clear()
        _geo_zones.clear()
        _zone_table_cache = None
        _known_zones_cache = None
