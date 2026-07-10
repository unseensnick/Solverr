"""Engine-related configuration read from environment variables.

Kept separate from utils.py (which holds the Chrome/Selenium helpers) so the
stealth engine's settings live in one place. All values are optional; defaults
keep the service behaving like stock FlareSolverr with the stealth engine
available but not the default.
"""
import os
import sys


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
    """Click-solver attempts per request. Empty/invalid => effectively unlimited,
    bounded by the request's maxTimeout (mirrors Byparr)."""
    raw = os.environ.get('STEALTH_MAX_ATTEMPTS', '').strip()
    if not raw:
        return sys.maxsize
    try:
        return int(raw)
    except ValueError:
        return sys.maxsize


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
