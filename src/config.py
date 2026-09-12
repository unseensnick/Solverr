"""Engine-related configuration read from environment variables.

Kept separate from utils.py (which holds the Chrome/Selenium helpers) so the
stealth engine's settings live in one place. All values are optional; defaults
keep the service behaving like stock FlareSolverr with the stealth engine
available but not the default.
"""
import logging
import os
import re
from typing import Optional


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() == 'true'


def stealth_enabled() -> bool:
    """Whether the Camoufox stealth engine should be loaded at startup."""
    return _bool('STEALTH_ENGINE', True)


def default_engine() -> str:
    """Engine used when a request doesn't specify one ('chrome' | 'stealth' | 'auto')."""
    return os.environ.get('DEFAULT_ENGINE', 'chrome').strip().lower()


def stealth_headless() -> bool:
    return _bool('STEALTH_HEADLESS', True)


def stealth_max_attempts() -> int:
    """Click attempts per click-solver nudge (default 1). The engine runs its own
    wait-and-retry loop bounded by the request's maxTimeout, so one attempt per
    nudge keeps each pass fast. Set STEALTH_MAX_ATTEMPTS to override."""
    return _int_env('STEALTH_MAX_ATTEMPTS', 1)


def stealth_start_timeout() -> float:
    """Seconds allowed to launch a Camoufox browser/context."""
    raw = os.environ.get('STEALTH_START_TIMEOUT', '120').strip()
    try:
        return float(raw)
    except ValueError:
        return 120.0


def engine_fallback() -> bool:
    """When a request doesn't force an engine, retry on the other engine if the
    first is blocked, times out, or returns an unsolved challenge page."""
    return _bool('ENGINE_FALLBACK', True)


# ---- Browser language (both engines) ----------------------------------------

# A language tag both engines accept: 2-3 letter language, optional 4-letter
# script, optional 2-letter or 3-digit region ('en', 'pt-BR', 'zh-Hans-CN').
_LANGUAGE_TAG = re.compile(r'^([A-Za-z]{2,3})(?:-([A-Za-z]{4}))?(?:-([A-Za-z]{2}|[0-9]{3}))?$')

# Warn once per bad value: this is read on every browser launch, so logging it
# each time would bury the rest of the request log.
_rejected_langs = set()


def browser_locale() -> Optional[str]:
    """The browser language for both engines: LANG, else BROWSER_GEO, else None.

    A LANG that is not a language falls through to BROWSER_GEO rather than
    suppressing it, because 'C.UTF-8' is a common container default that nobody
    set on purpose.
    """
    raw = os.environ.get('LANG', '').strip()
    return (_language_tag(raw, 'LANG') if raw else None) or browser_geo()


def browser_geo() -> Optional[str]:
    """BROWSER_GEO as a language tag: one setting for both the browser language
    and its timezone. LANG and BROWSER_TIMEZONE each win over it for their own
    half, so it is a convenience rather than another layer of precedence."""
    raw = os.environ.get('BROWSER_GEO', '').strip()
    return _language_tag(raw, 'BROWSER_GEO') if raw else None


def _language_tag(raw: str, source: str) -> Optional[str]:
    """A POSIX or BCP-47 value as a language tag, or None if it is neither.

    LANG is normally POSIX ('en_US.UTF-8', 'de_DE@euro'), which is not a language
    tag, and handing that over raw is worse than ignoring it. Camoufox builds
    both navigator.languages and the Accept-Language header from this value, so
    'en_US.UTF-8' would become navigator.languages = ["en-US.UTF-8", "en"], a
    pair no real browser produces and an obvious tell. Anything that does not
    normalize to a real tag is dropped instead, leaving Camoufox to derive one
    from the egress country and Chrome to send its own.
    """
    tag = raw.split('.')[0].split('@')[0].replace('_', '-')
    # 'C' and 'POSIX' are the locale-less locales, not languages.
    if tag.upper() in ('C', 'POSIX'):
        return None
    match = _LANGUAGE_TAG.match(tag)
    if not match:
        if raw not in _rejected_langs:
            _rejected_langs.add(raw)
            logging.warning("%s=%r is not a language tag; leaving the browser language alone",
                            source, raw)
        return None
    language, script, region = match.groups()
    parts = [language.lower()]
    if script:
        parts.append(script.title())
    if region:
        parts.append(region.upper())
    return '-'.join(parts)


def browser_timezone() -> Optional[str]:
    """BROWSER_TIMEZONE: an IANA zone to pin both engines to, 'auto', or None.

    'auto' and unset mean the same thing, deriving the zone from the egress IP
    so it agrees with the exit country. Pinning it to a zone costs no lookup, so
    an offline deployment sets this rather than an opt-out flag.

    A zone the system's tzdata does not list is dropped with a warning and the
    request falls back to 'auto', because an unchecked typo splits the engines:
    Chrome's CDP override fails and `_apply_timezone` swallows it, leaving that
    browser on the container's zone, while the stealth side hands the same
    string to Camoufox.
    """
    raw = os.environ.get('BROWSER_TIMEZONE', '').strip()
    if not raw or raw.lower() == 'auto':
        return raw or None
    return _known_zone(raw)


# Warn once per bad value, like the language reader above: this is read on every
# browser launch.
_rejected_zones = set()


def _known_zone(raw: str) -> Optional[str]:
    """`raw` if the system's timezone table lists it, else None ('auto').

    geo imports config, so the import stays inside the function. A host with no
    tzdata has nothing to check against, and geo already warns about that on its
    own, so the value is passed through unverified rather than dropped.
    """
    import geo
    if geo.is_known_zone(raw):
        return raw
    if raw not in _rejected_zones:
        _rejected_zones.add(raw)
        logging.warning("BROWSER_TIMEZONE=%r is not a timezone this system knows; "
                        "following the exit IP instead", raw)
    return None


def response_headers() -> bool:
    """Report the page's real response headers in `solution.headers`.

    Read by both engines together and never by one alone. A client cannot tell
    which engine answered a request, so headers appearing on one and not the
    other would be a difference it could not explain or rely on.

    Off by default for two reasons. The Chrome engine can only get them by
    asking the browser to log network events at launch, which changes what the
    browser is doing on every request and has not been measured against a
    fingerprinting check. And `solution.headers` has been an empty map since the
    fork, so populating it unasked would change every response.

    A launch setting rather than a request field, because the Chrome half has to
    be enabled before the browser starts.
    """
    return _bool('RESPONSE_HEADERS', False)


_warned_empty_proxy = False


def _warn_empty_proxy() -> None:
    """Say once that a set-but-blank PROXY_URL means no proxy.

    It used to fail every request instead, which was at least loud. Now traffic
    leaves on the server's own address, which is what the deployer was trying to
    avoid by setting the variable at all.
    """
    global _warned_empty_proxy
    _warned_empty_proxy = True
    logging.warning("PROXY_URL is set but empty, so requests go out directly. "
                    "Give it a proxy URL, or unset it to say so on purpose.")


def env_proxy() -> Optional[dict]:
    """The configured proxy as a request-shaped dict, or None when unset.

    One reader because the same rule was written out three times and the copies
    disagreed: the startup timezone lookup built `{"url": ...}` without the
    credentials, and geo caches per proxy *server* rather than per credential
    set, so an authenticated proxy refused that lookup and the fallback zone and
    language were cached for every request that followed. The browser then
    reported a country its exit IP did not match, which is the one pairing sites
    actually check.
    """
    url = os.environ.get('PROXY_URL')
    if not url:
        if url is not None and not _warned_empty_proxy:
            _warn_empty_proxy()
        return None
    username = os.environ.get('PROXY_USERNAME')
    password = os.environ.get('PROXY_PASSWORD')
    if username is None and password is None:
        return {"url": url}
    return {"url": url, "username": username, "password": password}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def browser_wait_timeout() -> int:
    """Seconds the Chrome engine waits for an expected page state on each attempt.

    Raise it on a slow host or a slow site, where one second is not enough for
    the challenge markup to go and every pass burns a click retry it did not
    need. Chrome only: the stealth engine has no fixed per-attempt wait, it
    polls until the request's own deadline.

    A value that is not a number falls back to the default rather than raising,
    unlike upstream's bare int(): the engine reads this mid-solve, so a raise
    there surfaces as "Error solving the challenge" and a silent fallback to the
    other engine, which tells nobody the variable is malformed.
    """
    return _int_env('BROWSER_WAIT_TIMEOUT', 1)


def session_ttl_minutes() -> int:
    """Idle minutes before the reaper closes a session's browser.

    Zero or less disables idle reaping entirely (``SessionStore.reap_idle``
    returns early), so abandoned browsers then live until the cap evicts them.
    The reaper says which of the two it is at startup.
    """
    return _int_env('SESSION_TTL_MINUTES', 30)


def session_max() -> int:
    """Max concurrent sessions per engine before the oldest-idle is evicted.

    Zero or less disables the cap (``SessionStore.enforce_cap`` returns early)
    rather than refusing every session, so the only bound left is the TTL.
    """
    return _int_env('SESSION_MAX', 20)


def reaper_interval_seconds() -> int:
    """How often the session reaper scans for idle/over-cap sessions."""
    return _int_env('REAPER_INTERVAL_SECONDS', 60)


# ---- Optional paid CAPTCHA API fallback (dormant unless configured) ----------

def captcha_provider() -> str:
    """CAPTCHA_SOLVER: 'none' (default), '2captcha'/'twocaptcha', 'capsolver', or any
    other 2captcha-compatible provider (set CAPTCHA_API_URL for it)."""
    return os.environ.get('CAPTCHA_SOLVER', 'none').strip().lower()


def captcha_api_key() -> str:
    return os.environ.get('CAPTCHA_API_KEY', '').strip()


def api_solver_enabled() -> bool:
    """The paid API solver runs only when a provider AND an API key are both set."""
    return captcha_provider() not in ('', 'none') and bool(captcha_api_key())


def captcha_api_server() -> str:
    """2captcha-compatible API host. Explicit CAPTCHA_API_URL wins; otherwise a
    per-provider default. CapSolver exposes a 2captcha-compatible endpoint."""
    override = os.environ.get('CAPTCHA_API_URL', '').strip()
    if override:
        return override
    if captcha_provider() == 'capsolver':
        return 'api.capsolver.com'
    return '2captcha.com'


def captcha_api_max_attempts() -> int:
    return _int_env('CAPTCHA_API_MAX_ATTEMPTS', 3)


def max_timeout_ms() -> int:
    """Ceiling on a request's maxTimeout (0 or less lifts it).

    Left unbounded, one request holds a browser for as long as the caller asks,
    and the session it marks in use cannot be reaped while it runs, so the
    reaper cannot reclaim that browser either. The default sits above the real
    worst case rather than at a round number: a request that legitimately
    succeeded in 133 seconds is on record after the budget was split evenly
    across engines, so a ceiling at or below 120000 would refuse work that
    currently completes.
    """
    return _int_env('MAX_TIMEOUT_MS', 180000)


# ---- Optional passthrough proxy (dormant unless enabled) ---------------------

def passthrough_enabled() -> bool:
    """Serve solved page bodies over plain HTTP on a second port. A client that
    would otherwise re-fetch the URL itself (tripping Cloudflare's fingerprinting
    on that replay) points at this port and consumes the solved HTML directly, so
    it never sees a challenge. Off by default."""
    return _bool('PASSTHROUGH_ENABLED', False)


def passthrough_port() -> int:
    return _int_env('PASSTHROUGH_PORT', 8888)


def passthrough_allowed_hosts() -> list:
    """Hosts the passthrough may fetch (the upstream is taken from the first path
    segment). Anything not listed is refused, so the listener is never a blind
    open proxy. Comma-separated; empty means refuse every request."""
    raw = os.environ.get('PASSTHROUGH_ALLOWED_HOSTS', '')
    return [h.strip().lower() for h in raw.split(',') if h.strip()]


def passthrough_cache_ttl() -> int:
    """Seconds to cache a solved 2xx body (0 disables caching)."""
    return _int_env('PASSTHROUGH_CACHE_TTL', 3600)


def passthrough_cache_requires() -> str:
    """A string a solved body must contain to earn the full cache TTL.

    Empty by default, which caches every 2xx body for the full TTL. A site
    answers a transient failure with its own error page, under HTTP 200 and with
    its usual layout around it, so nothing in the response says not to keep it:
    an hour of that is an indexer that looks broken while the site is fine.
    Naming something every real page carries (a link prefix its result rows use,
    a marker in its footer) gives the proxy a way to tell the two apart, and a
    body without it is kept only briefly instead of not at all, so a burst of
    identical requests still costs one solve.
    """
    return os.environ.get('PASSTHROUGH_CACHE_REQUIRES', '').strip()


def passthrough_cache_max_bytes() -> int:
    """Ceiling on the total bytes the response cache may hold (0 or less lifts it).

    The TTL bounds how long a body is kept, not how much: every distinct path a
    client asks for inside one TTL window accumulated with no ceiling, and a
    non-HTML document is held as decoded bytes, so a handful of large ones cost
    proportionally more than pages do. 256 MB is generous next to the image and
    the two browsers it runs.
    """
    return _int_env('PASSTHROUGH_CACHE_MAX_BYTES', 268435456)


def passthrough_timeout_ms() -> int:
    """maxTimeout handed to the solver for each passthrough request.

    Under the client's own patience rather than over it: an indexer app gives a
    request about 100 seconds, and a solve that outlives that is recorded as an
    indexer failure and puts it into backoff, which costs far more than the one
    request. 90 seconds leaves the answer (or the error) inside that window.
    """
    return _int_env('PASSTHROUGH_TIMEOUT_MS', 90000)
