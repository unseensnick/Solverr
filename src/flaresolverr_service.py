import logging
import platform
import sys
import threading
import time
from urllib.parse import urlparse

import config
import detection
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
    if req.maxTimeout is None or int(req.maxTimeout) < 1:
        req.maxTimeout = 60000

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

    engine = (req.engine or config.default_engine()).lower()
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
        session_ids = session_ids + STEALTH_ENGINE.session_ids()

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "",
        "sessions": session_ids
    })


def _cmd_sessions_destroy(req: V1RequestBase) -> V1ResponseBase:
    session_id = req.session
    existed = SESSIONS_STORAGE.destroy(session_id)
    if not existed and STEALTH_ENGINE is not None:
        existed = STEALTH_ENGINE.destroy_session(session_id)

    if not existed:
        raise Exception("The session doesn't exist.")

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "The session has been removed."
    })


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

    forced = (req.engine or '').strip().lower()
    if forced in ('chrome', 'stealth'):
        if forced not in available:
            raise Exception(f"Requested engine '{forced}' is not available.")
        return [available[forced]], False

    host = _host_of(req)
    primary = _recalled_engine(host)
    if primary not in available:
        primary = None
    if primary is None and req.session:
        for name in available:
            if _pool_has(name, req.session):
                primary = name
                break
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
    timeout = int(req.maxTimeout) / 1000
    order, _can_fallback = _engine_plan(req)
    host = _host_of(req)

    last_error = None
    for i, engine in enumerate(order):
        is_last = i == len(order) - 1
        try:
            result = engine.solve(req, method, timeout)
        except Exception as e:
            last_error = e
            if is_last:
                raise
            logging.warning("Engine '%s' failed (%s); falling back to '%s'...",
                            engine.name, e, order[i + 1].name)
            continue

        if not is_last and _looks_challenged(result):
            last_error = Exception(f"Engine '{engine.name}' returned an unsolved challenge page")
            logging.info("Engine '%s' returned an unsolved challenge page; falling back to '%s'...",
                         engine.name, order[i + 1].name)
            continue

        _remember_engine(host, engine.name)
        logging.info("Solved %s with engine '%s'", host or req.url, engine.name)
        return _to_challenge_resolution(result)

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
    if result.screenshot is not None:
        challenge_res.screenshot = result.screenshot

    res.result = challenge_res
    return res
