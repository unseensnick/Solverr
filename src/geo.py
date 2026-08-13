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

_lock = threading.Lock()
_cache = {}  # proxy server -> (expires_monotonic, zone)


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
    """Drop resolved zones. For tests."""
    with _lock:
        _cache.clear()
