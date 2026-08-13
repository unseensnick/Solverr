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
    raw = os.environ.get('STEALTH_MAX_ATTEMPTS', '').strip()
    if not raw:
        return 1
    try:
        return int(raw)
    except ValueError:
        return 1


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
    """LANG as a language tag for both engines, or None to leave it to them.

    LANG is normally POSIX ('en_US.UTF-8', 'de_DE@euro'), which is not a language
    tag, and handing that over raw is worse than ignoring it. Camoufox builds
    both navigator.languages and the Accept-Language header from this value, so
    'en_US.UTF-8' would become navigator.languages = ["en-US.UTF-8", "en"], a
    pair no real browser produces and an obvious tell. Anything that does not
    normalize to a real tag is dropped instead, leaving Camoufox to derive one
    from the egress country and Chrome to send its own.
    """
    raw = os.environ.get('LANG', '').strip()
    if not raw:
        return None
    tag = raw.split('.')[0].split('@')[0].replace('_', '-')
    # 'C' and 'POSIX' are the locale-less locales, not languages.
    if tag.upper() in ('C', 'POSIX'):
        return None
    match = _LANGUAGE_TAG.match(tag)
    if not match:
        if raw not in _rejected_langs:
            _rejected_langs.add(raw)
            logging.warning("LANG=%r is not a language tag; leaving the browser language alone", raw)
        return None
    language, script, region = match.groups()
    parts = [language.lower()]
    if script:
        parts.append(script.title())
    if region:
        parts.append(region.upper())
    return '-'.join(parts)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def session_ttl_minutes() -> int:
    """Idle minutes before the reaper closes a session's browser (0 disables reaping)."""
    return _int_env('SESSION_TTL_MINUTES', 30)


def session_max() -> int:
    """Max concurrent sessions per engine before the oldest-idle is evicted."""
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


def passthrough_timeout_ms() -> int:
    """maxTimeout handed to the solver for each passthrough request."""
    return _int_env('PASSTHROUGH_TIMEOUT_MS', 120000)
