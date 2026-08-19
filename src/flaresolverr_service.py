import logging
import os
import platform
import re
import sys
import threading
import time
from urllib.parse import urlparse

# Only these two schemes may reach a browser, matched on the literal prefix so
# nothing normalizes its way past the check. See _validate_url.
_HTTP_URL = re.compile(r'^https?://', re.IGNORECASE)

import config
import detection
import geo
import utils
from dtos import (STATUS_ERROR, STATUS_OK, ChallengeResolutionResultT,
                  ChallengeResolutionT, HealthResponse, IndexResponse,
                  V1RequestBase, V1ResponseBase)
from engines.base import SolveResult
from engines.chrome_engine import ChromeEngine
from sessions import SessionsStorage

SESSIONS_STORAGE = SessionsStorage()

# Chrome (Selenium + undetected_chromedriver) is the default engine and owns its
# own SessionsStorage. The stealth engine (Camoufox + playwright-captcha) is
# loaded lazily so the service still runs on a Chrome-only image or when its
# heavier dependencies aren't installed.
CHROME_ENGINE = ChromeEngine(SESSIONS_STORAGE)
STEALTH_ENGINE = None
if config.stealth_enabled():
    try:
        from engines.stealth_engine import StealthEngine
        STEALTH_ENGINE = StealthEngine()
    except Exception as e:
        logging.warning("Stealth engine unavailable, continuing with Chrome only: %s", e)


def test_browser_installation():
    logging.info("Testing web browser installation...")
    logging.info("Platform: " + platform.platform())

    chrome_exe_path = utils.get_chrome_exe_path()
    if chrome_exe_path is None:
        logging.error("Chrome / Chromium web browser not installed!")
        sys.exit(1)
    else:
        logging.info("Chrome / Chromium path: " + chrome_exe_path)

    chrome_major_version = utils.get_chrome_major_version()
    if chrome_major_version == '':
        logging.error("Chrome / Chromium version not detected!")
        sys.exit(1)
    else:
        logging.info("Chrome / Chromium major version: " + chrome_major_version)

    logging.info("Launching web browser...")
    user_agent = utils.get_user_agent()
    logging.info("Solverr User-Agent: " + user_agent)

    # Resolve the browser timezone once here, the way the user agent is, so no
    # request pays for the egress lookup. A per-request proxy still resolves on
    # first use; the configured one is the common case.
    env_proxy = {"url": os.environ.get('PROXY_URL')} if os.environ.get('PROXY_URL') else None
    logging.info("Browser timezone: " + geo.browser_timezone(geo.proxy_to_config(env_proxy)))

    logging.info("Test successful!")


def index_endpoint() -> IndexResponse:
    res = IndexResponse({})
    res.msg = "FlareSolverr is ready!"
    res.version = utils.get_flaresolverr_version()
    res.userAgent = utils.get_user_agent()
    return res


def health_endpoint() -> HealthResponse:
    res = HealthResponse({})
    res.status = STATUS_OK
    return res


def controller_v1_endpoint(req: V1RequestBase) -> V1ResponseBase:
    start_ts = int(time.time() * 1000)
    logging.info(f"Incoming request => POST /v1 body: {utils.object_to_dict(req)}")
    res: V1ResponseBase
    try:
        res = _controller_v1_handler(req)
    except Exception as e:
        res = V1ResponseBase({})
        res.__error_500__ = True
        res.status = STATUS_ERROR
        res.message = "Error: " + str(e)
        logging.error(res.message)

    res.startTimestamp = start_ts
    res.endTimestamp = int(time.time() * 1000)
    res.version = utils.get_flaresolverr_version()
    logging.debug(f"Response => POST /v1 body: {utils.object_to_dict(res)}")
    logging.info(f"Response in {(res.endTimestamp - res.startTimestamp) / 1000} s")
    return res


def _controller_v1_handler(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.cmd is None:
        raise Exception("Request parameter 'cmd' is mandatory.")
    if req.headers is not None:
        logging.warning("Request parameter 'headers' was removed in FlareSolverr v2.")
    if req.userAgent is not None:
        logging.warning("Request parameter 'userAgent' was removed in FlareSolverr v2.")

    # set default values
    _validate_max_timeout(req)

    # execute the command
    res: V1ResponseBase
    if req.cmd == 'sessions.create':
        res = _cmd_sessions_create(req)
    elif req.cmd == 'sessions.list':
        res = _cmd_sessions_list(req)
    elif req.cmd == 'sessions.destroy':
        res = _cmd_sessions_destroy(req)
    elif req.cmd == 'request.get':
        res = _cmd_request_get(req)
    elif req.cmd == 'request.post':
        res = _cmd_request_post(req)
    else:
        raise Exception(f"Request parameter 'cmd' = '{req.cmd}' is invalid.")

    return res


def _cmd_request_get(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.url is None:
        raise Exception("Request parameter 'url' is mandatory in 'request.get' command.")
    _validate_url(req.url)
    _validate_session_ttl(req)
    if req.postData is not None:
        raise Exception("Cannot use 'postBody' when sending a GET request.")
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'GET')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_request_post(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.postData is None:
        raise Exception("Request parameter 'postData' is mandatory in 'request.post' command.")
    _validate_url(req.url)
    _validate_session_ttl(req)
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'POST')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_sessions_create(req: V1RequestBase) -> V1ResponseBase:
    logging.debug("Creating new session...")

    engine = _validate_engine(req.engine) or config.default_engine().lower()
    if engine == 'stealth':
        if STEALTH_ENGINE is None:
            raise Exception("Stealth engine is not available (STEALTH_ENGINE disabled or dependencies missing).")
        session_id, fresh = STEALTH_ENGINE.create_session(session_id=req.session, proxy=req.proxy)
    else:
        session, fresh = SESSIONS_STORAGE.create(session_id=req.session, proxy=req.proxy)
        session_id = session.session_id

    if not fresh:
        return V1ResponseBase({
            "status": STATUS_OK,
            "message": "Session already exists.",
            "session": session_id
        })

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "Session created successfully.",
        "session": session_id
    })


def _cmd_sessions_list(req: V1RequestBase) -> V1ResponseBase:
    session_ids = SESSIONS_STORAGE.session_ids()
    if STEALTH_ENGINE is not None:
        # An engine fallback can leave the same id in both pools; report it once.
        session_ids = list(dict.fromkeys(session_ids + STEALTH_ENGINE.session_ids()))

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "",
        "sessions": session_ids
    })


def _cmd_sessions_destroy(req: V1RequestBase) -> V1ResponseBase:
    session_id = req.session
    existed = SESSIONS_STORAGE.destroy(session_id)
    if STEALTH_ENGINE is not None:
        # Always try both pools: an engine fallback can have created a browser
        # under the same id in the other one, and stopping at the first hit would
        # leave it running until the reaper notices.
        existed = STEALTH_ENGINE.destroy_session(session_id) or existed

    if not existed:
        raise Exception("The session doesn't exist.")

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "The session has been removed."
    })


# Below this, a fallback engine cannot launch a browser and reach a page, so
# spending what is left only replaces the first engine's error with a timeout.
_MIN_ENGINE_SECONDS = 5.0

# Per-domain memory of which engine last cleared a host, so a host that only the
# stealth engine can solve skips the failing Chrome attempt on later requests.
_DOMAIN_ENGINE = {}
_DOMAIN_LOCK = threading.Lock()


def _available_engines() -> dict:
    engines = {CHROME_ENGINE.name: CHROME_ENGINE}
    if STEALTH_ENGINE is not None:
        engines[STEALTH_ENGINE.name] = STEALTH_ENGINE
    return engines


def _pool_has(name: str, session_id: str) -> bool:
    if name == 'chrome':
        return SESSIONS_STORAGE.exists(session_id)
    if name == 'stealth' and STEALTH_ENGINE is not None:
        return STEALTH_ENGINE.exists(session_id)
    return False


def _validate_url(url) -> None:
    """Reject a URL a browser should never be pointed at.

    Solverr has no auth, so without this a `file://` or `data:` URL turns /v1
    into a local-file reader for anyone who can reach the port: the browser
    fetches it and the content comes back in `solution.response`.

    Anchored on the literal prefix, which is the shape Byparr validates against
    too. urlparse alone is looser than it looks: it strips leading whitespace
    before reading the scheme and accepts a single slash, so ' https://x' and
    'https:/x' both passed and were handed to a browser to normalize.
    """
    if not url:
        raise Exception("Request parameter 'url' is mandatory.")
    if not _HTTP_URL.match(url):
        raise Exception("Request parameter 'url' must be an 'http://' or 'https://' URL.")


def _validate_session_ttl(req: V1RequestBase) -> None:
    """Reject a session lifetime that cannot mean anything.

    A negative value makes a negative timedelta, which every session then
    compares as already expired, so the browser is rebuilt on every request and
    sessions quietly stop being sessions. Saying so beats looking broken.
    """
    ttl = req.session_ttl_minutes
    if ttl is None:
        return
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 0:
        raise Exception("Request parameter 'session_ttl_minutes' must be a positive number of minutes.")


def _validate_max_timeout(req: V1RequestBase) -> None:
    """Bound the request budget at both ends, and clamp it to the ceiling.

    Unbounded above, one request holds a browser for as long as the caller asks
    for, and the session it marks in use is skipped by the reaper for that whole
    time, so the browser cannot be reclaimed either. Clamping rather than
    refusing keeps a client that already asks for more than the ceiling working.

    The value is coerced rather than type-checked because a numeric string has
    always been accepted here and reaches the budget through int(); refusing one
    now would break callers that work today. Only a value that cannot be read as
    a number is refused, and it says so instead of raising ValueError from inside
    the budget arithmetic.
    """
    if req.maxTimeout is None:
        req.maxTimeout = 60000
        return
    if isinstance(req.maxTimeout, bool):
        raise Exception("Request parameter 'maxTimeout' must be a number of milliseconds.")
    try:
        value = int(req.maxTimeout)
    except (TypeError, ValueError):
        raise Exception("Request parameter 'maxTimeout' must be a number of milliseconds.")
    if value < 1:
        req.maxTimeout = 60000
        return
    ceiling = config.max_timeout_ms()
    if 0 < ceiling < value:
        logging.warning("Request parameter 'maxTimeout' of %dms is above the %dms ceiling and was "
                        "clamped. Raise MAX_TIMEOUT_MS if a longer budget is intended.", value, ceiling)
        value = ceiling
    req.maxTimeout = value


def _validate_engine(engine) -> str:
    """The requested engine lower-cased, or '' when unset. Raises on anything else.

    A misspelled name used to fall through to the default engine, so a caller
    that meant to pin one got an answer from the other and never found out.
    """
    forced = (engine or '').strip().lower()
    if forced in ('', 'auto', 'chrome', 'stealth'):
        return forced
    raise Exception(f"Request parameter 'engine' = '{engine}' is invalid. "
                    "Use 'chrome', 'stealth', or 'auto'.")


def _host_of(req: V1RequestBase):
    try:
        return urlparse(req.url).hostname
    except Exception:
        return None


def _remember_engine(host, name: str):
    if host:
        with _DOMAIN_LOCK:
            _DOMAIN_ENGINE[host] = name


def _recalled_engine(host):
    if not host:
        return None
    with _DOMAIN_LOCK:
        return _DOMAIN_ENGINE.get(host)


def _engine_plan(req: V1RequestBase):
    """Return (ordered_engines, can_fallback).

    An explicit ``engine`` forces a single engine (no fallback). Otherwise the
    primary is chosen from per-domain memory, then the engine already holding the
    request's session, then DEFAULT_ENGINE; the other engine is appended as a
    fallback when ENGINE_FALLBACK is on and both engines are available.
    """
    available = _available_engines()

    forced = _validate_engine(req.engine)
    if forced in ('chrome', 'stealth'):
        if forced not in available:
            raise Exception(f"Requested engine '{forced}' is not available.")
        return [available[forced]], False

    host = _host_of(req)
    # The engine holding the session wins: a session is a specific browser, and
    # sending the request elsewhere would silently open a second one under the
    # same id (and solve without the cookies the client warmed up).
    primary = None
    if req.session:
        for name in available:
            if _pool_has(name, req.session):
                primary = name
                break
    if primary is None:
        primary = _recalled_engine(host)
        if primary not in available:
            primary = None
    if primary is None:
        default = config.default_engine()
        primary = default if default in available else 'chrome'
        if primary not in available:
            primary = next(iter(available))

    order = [available[primary]]
    if config.engine_fallback():
        for name, eng in available.items():
            if name != primary:
                order.append(eng)
    return order, len(order) > 1


def _looks_challenged(result: SolveResult) -> bool:
    """Heuristic: does the returned HTML still look like an unsolved challenge?

    Catches the known failure where an engine reports success but hands back the
    "Just a moment..." page. Only applies when full HTML was returned.
    """
    return detection.looks_like_challenge_html(result.response)


def _resolve_challenge(req: V1RequestBase, method: str) -> ChallengeResolutionT:
    """Solve ``req``, trying each planned engine within one shared budget.

    ``maxTimeout`` is how long the caller is willing to wait for an answer, so it
    covers the whole request rather than each engine in turn. Handing the full
    value to every engine made a two-engine fallback take up to twice as long as
    asked, which is long enough to trip the caller's own timeout while Solverr
    still believed it was inside the budget.

    Each engine gets an even share of what is left rather than all of it, because
    a first engine that runs its budget out leaves nothing for the fallback.
    Measured on a host only Chrome can clear: with the whole budget, the stealth
    engine spent all 120s and Chrome never ran, turning a request that used to
    succeed into a failure. An even share also costs nothing when the first
    engine is quick, since the fallback then inherits almost all of the budget.
    """
    timeout = int(req.maxTimeout) / 1000
    deadline = time.monotonic() + timeout
    order, _can_fallback = _engine_plan(req)
    host = _host_of(req)

    last_error = None
    # A result an engine did return, that only looked unsolved. Kept so running
    # out of budget hands it back rather than turning it into an error.
    last_result = None
    for i, engine in enumerate(order):
        remaining = deadline - time.monotonic()
        if i > 0 and remaining < _MIN_ENGINE_SECONDS:
            # Too little left for another browser to launch and navigate. Stop
            # here so the caller gets the reason the previous engine failed,
            # instead of a timeout this one was always going to hit.
            logging.info("Budget spent after engine '%s'; not trying '%s' with %.1fs left",
                         order[i - 1].name, engine.name, remaining)
            break
        is_last = i == len(order) - 1
        share = remaining if is_last else remaining / (len(order) - i)
        try:
            result = engine.solve(req, method, share)
        except Exception as e:
            last_error = e
            if is_last:
                raise
            logging.warning("Engine '%s' failed (%s); falling back to '%s'...",
                            engine.name, e, order[i + 1].name)
            continue

        if not is_last and _looks_challenged(result):
            last_error = Exception(f"Engine '{engine.name}' returned an unsolved challenge page")
            last_result = result
            logging.info("Engine '%s' returned an unsolved challenge page; falling back to '%s'...",
                         engine.name, order[i + 1].name)
            continue

        _remember_engine(host, engine.name)
        logging.info("Solved %s with engine '%s'", host or req.url, engine.name)
        return _to_challenge_resolution(result)

    if last_result is not None:
        # No engine left to try, so hand back the page the last one did return.
        # That is what would have happened had it been last in the plan, and the
        # caller can judge the challenge page for itself.
        logging.info("No engine left to try; returning the last page received")
        return _to_challenge_resolution(last_result)
    raise last_error or Exception("All engines failed to solve the challenge.")


def _to_challenge_resolution(result: SolveResult) -> ChallengeResolutionT:
    """Map an engine's SolveResult into the FlareSolverr response DTO.

    Optional fields (headers/response/screenshot) are only set when populated so
    the serialized JSON matches FlareSolverr's original shape (unset fields are
    omitted, not emitted as null).
    """
    res = ChallengeResolutionT({})
    res.status = STATUS_OK
    res.message = result.message

    challenge_res = ChallengeResolutionResultT({})
    challenge_res.url = result.url
    challenge_res.status = result.status
    challenge_res.cookies = result.cookies
    challenge_res.userAgent = result.user_agent
    challenge_res.turnstile_token = result.turnstile_token
    if result.headers is not None:
        challenge_res.headers = result.headers
    if result.response is not None:
        challenge_res.response = result.response
    if result.content_type is not None:
        challenge_res.contentType = result.content_type
    if result.screenshot is not None:
        challenge_res.screenshot = result.screenshot

    res.result = challenge_res
    return res
