"""Stealth engine: Camoufox (via invisible_playwright) + playwright-captcha.

Mirrors Byparr's proven solving stack and adds FlareSolverr feature parity
(sessions, POST, cookie injection, screenshot, returnOnlyCookies, proxy) plus the
shared broad challenge detection so it is not a reduced subset of the Chrome
engine. All Playwright work runs on the shared background event loop
(async_runtime); persistent per-session contexts live there so cookies survive
across requests.
"""
import asyncio
import base64
import logging
import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import timedelta
from urllib.parse import urlsplit
from typing import List, Optional, Tuple

from invisible_playwright.async_api import InvisiblePlaywright
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType, TwoCaptchaSolver

import assembly
import budget
import config
import geo
import pipeline
import utils
from async_runtime import get_runtime
from detection import INTERSTITIAL_SELECTORS, TURNSTILE_SELECTORS
from dtos import V1RequestBase
from engines.base import Engine, SolveResult
from postform import build_post_html
from sessions import SessionStore

# Best-effort settle waits are bounded; the hard navigation cap comes from the
# request's maxTimeout via asyncio.wait_for in _do_solve.
_NETWORKIDLE_MS = 5000

# What disableMedia blocks. Playwright names resource types, Chrome matches URL
# patterns (_MEDIA_BLOCK_URLS there), and both spell out the same three kinds:
# one request option cannot mean different things on the two engines.
_BLOCKED_RESOURCE_TYPES = ("image", "stylesheet", "font")

# A returned document is base64-encoded on top of the raw bytes and copied again
# by the JSON response, so cap what we are willing to pull into memory.
_MAX_PDF_BYTES = 32 * 1024 * 1024

# The Turnstile widget renders in an iframe served from this host, and its
# checkbox sits at the left edge, vertically centered, in the standard 300x65
# layout. Measured against the live widget: a click there flips it to "Success!".
# The same inset serves either rect: measured against the live widget, its
# container shares the iframe's x and width and is 7px taller.
_TURNSTILE_FRAME_HOST = "challenges.cloudflare.com"
_TURNSTILE_CHECKBOX_X = 30

# Ancestors of the Turnstile input to try when the widget's own iframe cannot be
# measured, nearest first, with the box guards that tell a checkbox row from a
# full-page wrapper. Ported from Byparr (ThePhaseless/Byparr#400), which measured
# these depths against live interstitials.
_WIDGET_ANCESTOR_DEPTHS = (1, 2, 3, 4)
_WIDGET_MIN_WIDTH = 40
_WIDGET_MIN_HEIGHT = 20
_WIDGET_MAX_HEIGHT = 120

# Reads against a challenge page are bounded well under the request budget:
# Playwright's own default is 30 seconds, which would spend the whole solve
# waiting for one element that a later pass would have found anyway.
_ELEMENT_READ_MS = 1000
_WIDGET_READ_SECONDS = 5

# Verifying takes a few seconds after a press, and pressing again over the top
# of it restarts the verification, so a landed press earns a cooldown while a
# press that found nothing to hit may retry on the next pass.
_CLICK_COOLDOWN_SECONDS = 4

# What the paid CAPTCHA escalation is allowed, kept back from the solve deadline
# when one is configured. It is a round trip to the provider and back, and a
# 2captcha Turnstile answer takes tens of seconds; without the reservation it
# began with only the response margin left and never finished.
_API_SOLVE_SECONDS = 30

# How long a browser gets to shut down. Generous because cutting the teardown
# short is worse than waiting: the step that reaps the browser process runs last.
_CLOSE_TIMEOUT_SECONDS = 60
_POLL_SECONDS = 1.5

# Cloudflare drops the challenge markup while it issues the next round, so a
# single clear reading does not mean the challenge is over. Look again after
# this long before believing it.
_CHALLENGE_CONFIRM_SECONDS = 1.0

# playwright-captcha logs each failed click attempt at ERROR, which is expected
# and harmless for non-interactive interstitials (no checkbox to click). We handle
# the solve outcome ourselves, so quiet its internal noise to keep logs readable.
logging.getLogger("playwright_captcha").setLevel(logging.CRITICAL)


# Keys Playwright's add_cookies accepts. Anything else (notably Selenium's
# 'expiry', which is what the Chrome engine returns) is rejected by its schema.
_PLAYWRIGHT_COOKIE_KEYS = ("name", "value", "url", "domain", "path", "expires",
                           "httpOnly", "secure", "sameSite")


def _to_client_cookies(cookies: list) -> list:
    """Playwright cookies to the shape FlareSolverr clients expect.

    Upstream returns Selenium's cookies, so a client sees 'expiry' as an int and
    no key at all for a session cookie. Playwright says 'expires' as a float and
    -1 for session cookies; translating here keeps a solve indistinguishable
    whichever engine handled it.
    """
    converted = []
    for cookie in cookies:
        cookie = dict(cookie)
        expires = cookie.pop("expires", None)
        if expires is not None and expires > 0:
            cookie["expiry"] = int(expires)
        converted.append(cookie)
    return converted


def _to_playwright_cookies(cookies: list, url: str) -> list:
    """Client-supplied cookies to Playwright's shape, accepting either dialect.

    Playwright refuses a cookie carrying neither a url nor a domain/path pair,
    and refuses the whole batch with it, so `{"name": "a", "value": "1"}` (the
    shape the README documents and the one FlareSolverr clients send) failed the
    entire request on this engine while working on the Chrome one.

    A cookie that does not say where it belongs is filled in from the request
    URL: its host as the domain and "/" as the path, which is what Selenium's
    add_cookie does with the same cookie. Not `url`, even though Playwright
    accepts one: `url` and `path` are mutually exclusive there, so a cookie that
    named a path and no domain still failed the whole request, and `url` alone
    scopes the cookie to that URL's directory rather than to the whole site.
    """
    host = urlsplit(url).hostname or ""
    converted = []
    for cookie in cookies:
        translated = {k: v for k, v in cookie.items() if k in _PLAYWRIGHT_COOKIE_KEYS}
        if "expires" not in translated and cookie.get("expiry") is not None:
            translated["expires"] = float(cookie["expiry"])
        if not translated.get("url"):
            if not translated.get("domain"):
                translated["domain"] = host
            translated.setdefault("path", "/")
        converted.append(translated)
    return converted


async def _present(page, selector) -> bool:
    """Whether a selector matches, as the shared verdict rule expects it."""
    return await page.query_selector(selector) is not None


async def _value(value):
    """A plain value as something awaitable, so every assembly read looks alike."""
    return value


def _user_agent_from(main_response) -> str:
    """The user agent the site was actually sent, or "" if it can't be read.

    The context caches its user agent once at start via page.evaluate, which
    Firefox blocks under a strict CSP, and a failure there would otherwise leave
    every response from that context reporting no user agent at all. The
    navigation request's headers are what the server saw and need no eval.
    """
    if main_response is None:
        return ""
    try:
        return main_response.request.headers.get("user-agent", "") or ""
    except Exception:
        logging.debug("could not read the user agent off the navigation request", exc_info=True)
        return ""


class StealthContext:
    """A live Camoufox browser + context + page + click-solver.

    Owned by and only ever touched from the background event loop. Reused across
    requests when attached to a session; created and torn down per-request
    otherwise.
    """

    def __init__(self, proxy_config: Optional[dict]):
        self.proxy_config = proxy_config
        self.lock = asyncio.Lock()
        self._ip = None
        self.browser = None
        self.context = None
        self.page = None
        self.user_agent = ""

    async def start(self):
        # Resolved here, not left to the library: handing it a concrete zone
        # returns before its own auto-resolution, which behind a proxy raises on
        # a failed egress lookup and takes the whole launch down with it. Off the
        # loop because a cold lookup blocks for seconds.
        timezone, language = await asyncio.to_thread(geo.browser_identity, self.proxy_config)
        self._ip = InvisiblePlaywright(
            headless=config.stealth_headless(),
            proxy=self.proxy_config,
            humanize=True,
            timezone=timezone,
            # Concrete rather than "auto" so this browser and Chrome get the
            # same country, and so the library resolves nothing of its own.
            locale=language,
            # Firefox renders application/json in a built-in viewer, so
            # page.content() would hand back the viewer's markup instead of the
            # payload. With it off, JSON renders as text in a <pre>, which is
            # what the Chrome engine already returns for the same URL.
            extra_prefs={"devtools.jsonview.enabled": False},
        )
        self.browser = await self._ip.__aenter__()
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        # Capture the UA now, on the blank page: challenge pages set a strict CSP
        # that blocks eval(), which page.evaluate() relies on in Firefox/Camoufox.
        try:
            self.user_agent = await self.page.evaluate("() => navigator.userAgent")
        except Exception:
            logging.debug("could not capture stealth user agent", exc_info=True)

    async def close(self):
        if self._ip is not None:
            try:
                await self._ip.__aexit__(None, None, None)
            except Exception:
                logging.debug("stealth browser teardown failed", exc_info=True)


class StealthEngine(Engine):
    name = "stealth"

    def __init__(self):
        self._runtime = get_runtime()
        self._sessions = SessionStore(build=self._start_context,
                                      teardown=self._close_context)
        # How long the request on this thread may spend launching a browser.
        # A session's browser is launched from inside SessionStore.get, which
        # fixes the signature, so the share reaches _start_context this way.
        self._launch_budget = threading.local()

    # ---- session registry (controller-facing) -------------------------------
    #
    # Every rule below lives in sessions.SessionStore, shared with the Chrome
    # engine: idempotent creation, the in-use mark that keeps the reaper off a
    # live browser, TTL expiry, idle reaping and the cap. This engine supplies
    # only how to start and close what a session holds.

    def _start_context(self, proxy: Optional[dict]) -> "StealthContext":
        ctx = StealthContext(geo.proxy_to_config(proxy))
        allowed = min(getattr(self._launch_budget, "seconds", None) or config.stealth_start_timeout(),
                      config.stealth_start_timeout())
        try:
            self._runtime.run(ctx.start(), timeout=allowed)
        except Exception:
            # start() may already have launched the browser before failing.
            self._close_context(ctx)
            raise
        return ctx

    def _close_context(self, ctx: "StealthContext") -> None:
        self._close_bounded(ctx)

    def _close_bounded(self, ctx: "StealthContext") -> None:
        """Close a context, and say so when the cap cuts the teardown short.

        The library closes the browser and then stops the driver, and only the
        second step reaps the browser process, so a teardown cancelled between
        them leaves one behind. Nothing here can finish that job, so the cap is
        generous and a request to look is louder than debug.
        """
        try:
            self._runtime.run(ctx.close(), timeout=_CLOSE_TIMEOUT_SECONDS)
        except FuturesTimeout:
            logging.warning("stealth browser did not shut down within %ss; a browser process "
                            "may be left behind", _CLOSE_TIMEOUT_SECONDS)

    def session_ids(self) -> List[str]:
        return self._sessions.session_ids()

    def exists(self, session_id: str) -> bool:
        return self._sessions.exists(session_id)

    def create_session(self, session_id: Optional[str] = None, proxy: Optional[dict] = None,
                       force_new: bool = False) -> Tuple[str, bool]:
        session, fresh = self._sessions.create(session_id, proxy, force_new)
        return session.session_id, fresh

    def destroy_session(self, session_id: str) -> bool:
        return self._sessions.destroy(session_id)

    def touch(self, session_id: str) -> None:
        self._sessions.touch(session_id)

    def reap_idle(self, ttl: timedelta) -> List[str]:
        return self._sessions.reap_idle(ttl)

    def enforce_cap(self, max_sessions: int) -> List[str]:
        return self._sessions.enforce_cap(max_sessions)

    # ---- solving ------------------------------------------------------------

    def solve(self, req: V1RequestBase, method: str, timeout: float) -> SolveResult:
        # The share starts here, not once the browser is up. Launching Camoufox
        # takes seconds, and they used to be spent outside the budget: a session
        # launch was bounded only by STEALTH_START_TIMEOUT, and a per-request one
        # could take a full share of its own before the solve clock started.
        started = time.monotonic()
        self._launch_budget.seconds = timeout
        own_ctx = False
        # get() hands the session over already marked in use, so the reaper and
        # the cap cannot close the browser under this request. Released in the
        # finally below, exactly as the Chrome engine does it.
        in_use = None
        # The session lock this request holds, released in the finally below.
        locked = None
        ctx = None
        # Inside the try, exactly as the Chrome engine does it: a session whose
        # browser fails to launch used to escape unwrapped, so the client got a
        # bare message (an empty one on a launch timeout) instead of the
        # "Error solving the challenge." the other engine reports.
        try:
            if req.session:
                ttl = timedelta(minutes=req.session_ttl_minutes) if req.session_ttl_minutes else None
                session, _ = self._sessions.get(req.session, ttl, req.proxy)
                in_use = session
                # One request at a time on this browser, the same rule the
                # Chrome engine follows, taken before the context's own lock so
                # the wait happens on the request thread and counts against the
                # share rather than queueing up on the event loop.
                if not session.lock.acquire(timeout=budget.remaining_share(started, timeout)):
                    raise Exception("Timed out waiting for session '%s' to be free." % req.session)
                locked = session.lock
                ctx = session.payload
            else:
                ctx = StealthContext(geo.proxy_to_config(req.proxy))
                # Owned before start(): a launch that fails or times out has
                # usually already spawned the browser, and only the finally
                # below closes it.
                own_ctx = True
            if own_ctx:
                self._runtime.run(ctx.start(),
                                  timeout=min(budget.remaining_share(started, timeout),
                                              config.stealth_start_timeout()))
            left = budget.remaining_share(started, timeout)
            return self._runtime.run(self._do_solve(req, ctx, method, left), timeout=left + 5)
        except FuturesTimeout:
            raise Exception(f'Error solving the challenge. Timeout after {timeout} seconds.')
        except Exception as e:
            raise Exception('Error solving the challenge. ' + str(e).replace('\n', '\\n'))
        finally:
            self._launch_budget.seconds = None
            if locked is not None:
                locked.release()
            if in_use is not None:
                self._sessions.end_use(in_use)
            if own_ctx and ctx is not None:
                try:
                    self._close_bounded(ctx)
                except Exception:
                    logging.debug("stealth ctx teardown failed", exc_info=True)

    async def _do_solve(self, req: V1RequestBase, ctx: StealthContext, method: str,
                        timeout: float) -> SolveResult:
        # The timeout covers the wait for the context as well as the solve. It
        # used to start only once the lock was taken, while the caller's hard cap
        # had been running since the request was submitted, so a queued request
        # was killed by the outer one with no verdict to hand back.
        return await asyncio.wait_for(self._locked_solve(req, ctx, method, timeout),
                                      timeout=timeout)

    async def _locked_solve(self, req: V1RequestBase, ctx: StealthContext, method: str,
                            timeout: float) -> SolveResult:
        loop = asyncio.get_running_loop()
        queued_at = loop.time()
        async with ctx.lock:
            left = max(1.0, timeout - (loop.time() - queued_at))
            return await self._navigate_and_solve(req, ctx, method, left)

    async def _navigate_and_solve(self, req: V1RequestBase, ctx: StealthContext,
                                  method: str, timeout: float) -> SolveResult:
        page = ctx.page
        started = asyncio.get_running_loop().time()

        disable_media = utils.get_config_disable_media()
        if req.disableMedia is not None:
            disable_media = req.disableMedia

        # Last main-frame document response, whichever page we end up on. A
        # page.goto() return value is not enough: after a solved challenge the real
        # document arrives in a later navigation.
        main_response = None
        instrumented = []

        async def instrument(target):
            block_handler = None
            if disable_media:
                async def block_handler(route):
                    # The same three kinds the Chrome engine blocks and the
                    # README promises. Byparr blocks media here instead of
                    # stylesheets, which made one option mean two things.
                    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                        await route.abort()
                    else:
                        await route.continue_()
                await target.route("**/*", block_handler)

            def remember_main_response(response):
                nonlocal main_response
                try:
                    # is_navigation_request() first: reading .frame raises for a
                    # request issued before its frame exists (a Turnstile iframe
                    # does this), and an exception here surfaces on an unrelated
                    # later call.
                    if response.request.is_navigation_request() and response.frame is target.main_frame:
                        main_response = response
                except Exception:
                    logging.debug("could not classify a response", exc_info=True)

            target.on("response", remember_main_response)
            instrumented.append((target, block_handler, remember_main_response))

        async def navigate(target):
            # timeout=0 defers to the outer asyncio.wait_for(maxTimeout) hard cap.
            # wait_until="domcontentloaded" avoids hanging on the full "load" event,
            # which a Cloudflare-gated API endpoint can hold open past the timeout.
            if method == "POST":
                await target.goto("data:text/html;charset=utf-8," + build_post_html(req.url, req.postData),
                                  wait_until="domcontentloaded", timeout=0)
                try:  # wait for the auto-submit to navigate to the POST target
                    await target.wait_for_load_state("domcontentloaded", timeout=_NETWORKIDLE_MS)
                except Exception:
                    logging.debug("post-submit load wait timed out")
            else:
                await target.goto(req.url, wait_until="domcontentloaded", timeout=0)

        click = None

        async def open_click_page():
            """Move onto a throwaway page that carries the solver's patches.

            Preparing a solver injects init scripts (notably one that rewrites
            Element.prototype.attachShadow) which are what let it see into a
            Turnstile's closed shadow root. Cloudflare's non-interactive
            interstitial refuses to clear while they are present, and Playwright
            cannot remove an init script once added, so they stay on a page we
            throw away: the context's own page keeps solving interstitials.
            """
            nonlocal click, page
            if click is None:
                target = await ctx.context.new_page()
                # Registered before the solver is prepared: preparing it can
                # raise or be cancelled, and the teardown below only closes what
                # is registered, so the page stayed open in a session's context.
                click = (target, None, None)
                solver_cm = ClickSolver(framework=FrameworkType.PLAYWRIGHT, page=target,
                                        max_attempts=config.stealth_max_attempts(),
                                        attempt_delay=1)
                click = (target, solver_cm, await solver_cm.__aenter__())
                await instrument(target)
                page = target
                await navigate(target)
            return click[2]

        try:
            await instrument(page)
            logging.debug(f"Navigating to... {req.url}")

            # The navigate-then-cookies-then-navigate order is shared with the
            # Chrome engine (pipeline.py); only the two steps below are this
            # engine's.
            await pipeline.run_async(
                {pipeline.Step.NAVIGATE: lambda _arg: navigate(page),
                 pipeline.Step.SET_COOKIES:
                     lambda cookies: ctx.context.add_cookies(
                         _to_playwright_cookies(cookies, req.url))},
                kernel=pipeline.approach, req=req)

            if utils.get_config_log_html():
                logging.debug(f"Response HTML:\n{await page.content()}")

            kind, is_turnstile = await self._detect_settled(page)
            if kind == "none":
                # An async challenge or widget (e.g. a Turnstile injected via api.js
                # after domcontentloaded) may not be in the DOM yet; settle and recheck.
                try:
                    await page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_MS)
                except Exception:
                    logging.debug("networkidle wait timed out")
                kind, is_turnstile = await self._detect_settled(page)

            if kind == "denied":
                raise Exception(pipeline.BLOCKED_MESSAGE)

            if kind == "challenge":
                captcha_type = (CaptchaType.CLOUDFLARE_TURNSTILE if is_turnstile
                                else CaptchaType.CLOUDFLARE_INTERSTITIAL)
                logging.info("Challenge detected. Solving with stealth engine (%s)...",
                             captcha_type.name)
                # Budget what is left of maxTimeout, not the whole of it: navigation
                # and detection already spent some. Overrunning it would let the
                # outer wait_for fire first and turn a clean "still challenged"
                # verdict (which the controller can retry on the other engine) into
                # a timeout error.
                deadline = budget.solve_deadline(started, timeout)
                if config.api_solver_enabled():
                    # Leave the escalation room to work. It is a network round
                    # trip to the provider and back, so starting it after the
                    # solve deadline left it whatever the response margin was,
                    # and configuring a paid solver turned "still challenged"
                    # (which the other engine can retry) into a timeout.
                    deadline -= _API_SOLVE_SECONDS
                # Both kinds are handled on the context's own page: an interstitial
                # clears itself, and a widget is clicked by coordinate, so neither
                # needs the solver's init scripts. Only the paid escalation below
                # does, and it moves to the throwaway page for them.
                solved = await self._wait_until_cleared(page, deadline)

                # Escalate to the paid CAPTCHA API only if configured and still
                # stuck. The budget for it was kept back above.
                if not solved and config.api_solver_enabled():
                    logging.info("Escalating to paid CAPTCHA API solver (%s)...",
                                 config.captcha_provider())
                    await open_click_page()
                    await self._api_solve(page, captcha_type)
                    solved = (await self._detect_settled(page))[0] != "challenge"

                if not solved:
                    raise Exception("Challenge still present after solving attempts")
                # Let the post-challenge navigation to the real page settle before
                # we read the content. Both states, because the destination is a
                # separate document that the interstitial only submits for once its
                # markup is gone: waiting for domcontentloaded alone returns at once
                # whenever the challenge page itself is still the current document.
                # Bounded by the same deadline the solve was, not a fixed
                # 5s per state: two of those on top of a solve that ran to its
                # deadline overran the share and turned a late clear into a
                # timeout error instead of the page it had just reached.
                loop = asyncio.get_running_loop()
                for state in ("domcontentloaded", "networkidle"):
                    left_ms = int((deadline - loop.time()) * 1000)
                    if left_ms <= 0:
                        logging.debug("no budget left to let the %s state settle", state)
                        break
                    try:
                        await page.wait_for_load_state(state, timeout=min(_NETWORKIDLE_MS, left_ms))
                    except Exception:
                        logging.debug("post-solve %s wait timed out", state)
                logging.info("Challenge solved!")
                message = "Challenge solved!"
            else:
                logging.info("Challenge not detected!")
                message = "Challenge not detected!"

            async def user_agent():
                if not ctx.user_agent:
                    # Backfill the context so a session that started without one
                    # recovers for its later requests too, not just this response.
                    ctx.user_agent = _user_agent_from(main_response)
                return ctx.user_agent

            async def token():
                # Parity with the Chrome engine: return the Turnstile token when
                # a standalone widget is present.
                return await self._turnstile_token(page) if is_turnstile else None

            async def body():
                # Firefox opens a PDF in its viewer, so page.content() would hand
                # back the viewer rather than the file. This is the one read where
                # the two engines genuinely differ in what they can produce.
                pdf = await self._pdf_body(page, main_response)
                if pdf is not None:
                    return pdf, "application/pdf"
                return await page.content(), None

            async def cookies():
                # Scoped to the page, not the whole context. A context outlives
                # the request when it is a session, so an unscoped read hands
                # back every host the session has ever visited, and clients add
                # returned cookies by name with no domain check. The Chrome
                # engine reports the active document's cookies only, which is
                # what this matches.
                return _to_client_cookies(await ctx.context.cookies(page.url))

            async def headers():
                # Already tracked for PDF detection, so this costs nothing extra
                # here. Gated on the same setting as the Chrome engine so a
                # client cannot tell the two apart by what it gets back.
                if not config.response_headers() or main_response is None:
                    return {}
                try:
                    # Playwright's headers are already lower-cased and joined on
                    # ", " for repeats, which is the shape Chrome's CDP map has,
                    # so both engines hand back the same keys for one page.
                    return dict(main_response.headers)
                except Exception:
                    logging.debug("could not read the response headers", exc_info=True)
                    return {}

            # Order and field rules live in assembly.py, shared with the Chrome
            # engine. Only the reads below are this engine's.
            return await assembly.run_async(req, message, {
                assembly.Read.URL: lambda: _value(page.url),
                assembly.Read.USER_AGENT: user_agent,
                assembly.Read.TOKEN: token,
                assembly.Read.HEADERS: headers,
                assembly.Read.WAIT: lambda: asyncio.sleep(req.waitInSeconds),
                assembly.Read.BODY: body,
                assembly.Read.SCREENSHOT: page.screenshot,
                assembly.Read.COOKIES: cookies,
            })
        finally:
            for target, block_handler, response_handler in instrumented:
                target.remove_listener("response", response_handler)
                if block_handler is not None:
                    try:
                        await target.unroute("**/*", block_handler)
                    except Exception:
                        logging.debug("unroute failed", exc_info=True)
            if click is not None:
                try:
                    if click[1] is not None:
                        await click[1].__aexit__(None, None, None)
                except Exception:
                    logging.debug("click solver teardown failed", exc_info=True)
                try:
                    await click[0].close()
                except Exception:
                    logging.debug("click page teardown failed", exc_info=True)

    async def _pdf_body(self, page, main_response) -> Optional[str]:
        """Base64 of the raw PDF when the page is a PDF document, else None.

        Firefox opens PDFs in its built-in viewer, so page.content() would hand
        back the viewer's HTML instead of the file. The browser already
        downloaded the bytes, so take them from the response it received.
        """
        if main_response is None:
            return None
        content_type = main_response.headers.get("content-type", "")
        if not content_type.lower().startswith("application/pdf"):
            return None
        try:
            data = await main_response.body()
        except Exception:
            logging.debug("PDF body no longer buffered, refetching", exc_info=True)
            data = await self._refetch_pdf(page, main_response)
        if data is None:
            return None
        if len(data) > _MAX_PDF_BYTES:
            logging.warning("PDF is %d bytes, past the %d byte cap; returning the viewer page instead",
                            len(data), _MAX_PDF_BYTES)
            return None
        return base64.b64encode(data).decode("ascii")

    async def _refetch_pdf(self, page, main_response) -> Optional[bytes]:
        """Re-download the PDF through the page's request context, or None.

        This request is not the browser's, so Cloudflare can answer it with a
        challenge page instead of the file. Only take the bytes when the second
        response agrees it is a PDF, otherwise the caller would label an error
        page 'application/pdf'.
        """
        try:
            fetched = await page.request.fetch(main_response.url)
            if fetched.ok and fetched.headers.get("content-type", "").lower().startswith("application/pdf"):
                return await fetched.body()
            logging.warning("PDF refetch answered %s (%s); returning the viewer page instead",
                            fetched.status, fetched.headers.get("content-type", "unknown"))
        except Exception as e:
            # Message only, never exc_info: Playwright's call log repeats the
            # request headers, which carry the solved cf_clearance cookie.
            logging.warning("Could not refetch the PDF (%s); returning the viewer page instead",
                            str(e).split("\nCall log:")[0].strip())
        return None

    async def _wait_until_cleared(self, page, deadline) -> bool:
        """Wait for the Cloudflare challenge to clear, up to ``deadline``.

        Non-interactive interstitials solve themselves after a few seconds of JS,
        so for those this just polls for the challenge to disappear. An interactive
        Turnstile needs a click, so its checkbox is clicked and re-clicked while it
        stays unsolved. The paid escalation does not come through here: it runs
        playwright-captcha on its own throwaway page.
        """
        loop = asyncio.get_running_loop()
        last_click = 0.0
        while True:
            # Both readings, every pass. A checkbox Cloudflare injects after the
            # first look used to go unclicked for the whole wait, because whether
            # there was one to click was decided once, before this loop began.
            kind, has_widget = await self._detect_settled(page)
            if kind == "denied":
                raise Exception(pipeline.BLOCKED_MESSAGE)
            if kind == "challenge":
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    logging.debug("challenge still present (title=%r, url=%s)",
                                  await page.title(), page.url)
                if has_widget:
                    token = await self._turnstile_token(page)
                    # A standalone Turnstile widget stays in the DOM after solving,
                    # so _detect keeps seeing it and only the filled token says it
                    # cleared. An interstitial carries the same input but still has
                    # to submit it and navigate, so there the token means the
                    # challenge is progressing and the markup going is what ends it.
                    if token and not await self._is_interstitial(page):
                        return True
                    # Never press over a box that already carries a token: that
                    # restarts Cloudflare's verification instead of completing it.
                    if not token and loop.time() - last_click >= _CLICK_COOLDOWN_SECONDS:
                        if await self._click_turnstile(page):
                            last_click = loop.time()
            elif await self._challenge_stays_gone(page):
                return True

            if loop.time() >= deadline:
                return False
            await asyncio.sleep(_POLL_SECONDS)

    async def _challenge_stays_gone(self, page) -> bool:
        """Whether a clear reading holds up when the page is looked at again.

        Cloudflare drops the challenge markup while it issues the next round, so
        a single clear reading is not the end of the challenge: one request here
        logged four distinct __cf_chl_tk tokens. Believing the first one hands
        back an intermediate challenge page as if it were the destination.
        """
        await asyncio.sleep(_CHALLENGE_CONFIRM_SECONDS)
        return (await self._detect_settled(page))[0] != "challenge"

    async def _is_interstitial(self, page) -> bool:
        """Whether a full-page challenge's own markup is on the page.

        Distinguishes it from a site's standalone Turnstile widget, which shares
        the token input but nothing else. Uses the narrow INTERSTITIAL_SELECTORS
        rather than the full challenge list, which carries markers an embedded
        widget shares and shapes an ordinary page can match by accident.

        Read past a navigation, because this is asked exactly when the token has
        just filled, which is the moment an interstitial submits it and leaves.
        """
        async def read():
            for sel in INTERSTITIAL_SELECTORS:
                if await page.query_selector(sel):
                    return True
            return False

        return await self._past_navigation(read)

    async def _click_turnstile(self, page) -> bool:
        """Click the Turnstile checkbox, without touching main-world JS.

        A coordinate click is dispatched by the browser rather than by page JS, so
        neither the widget's closed shadow root nor the iframe's CSP is in the way.
        Measuring it with page.evaluate would be, and worse: Byparr measured that
        running page scripts against a live challenge makes Cloudflare reissue it.
        """
        box = await self._widget_box(page)
        if box is None:
            return False
        await page.mouse.click(box["x"] + _TURNSTILE_CHECKBOX_X,
                               box["y"] + box["height"] / 2)
        return True

    async def _widget_box(self, page):
        """The Turnstile widget's rect, from its iframe or from its container.

        The iframe is the exact rect and is what a site's standalone widget
        exposes, so it is tried first. Cloudflare's own interstitial builds the
        widget itself and its challenge frame reports an empty URL to the parent,
        which is what makes the frame tree unusable there (measured by Byparr,
        ThePhaseless/Byparr#400, and the same reason playwright-captcha's
        page.frames fallback cannot find it either). The container around the
        token input is in the page's light DOM either way.
        """
        try:
            box = await asyncio.wait_for(self._frame_box(page), _WIDGET_READ_SECONDS)
        except Exception:
            logging.debug("turnstile frame read did not answer", exc_info=True)
            box = None
        return box if box is not None else await self._container_box(page)

    async def _frame_box(self, page):
        """The widget iframe's rect, or None when no frame owns up to being one."""
        for frame in page.frames:
            if _TURNSTILE_FRAME_HOST not in (frame.url or ""):
                continue
            try:
                box = await (await frame.frame_element()).bounding_box()
            except Exception:
                logging.debug("turnstile frame read raced a navigation", exc_info=True)
                continue
            # A widget that has not laid out yet reports no box; a later pass gets it.
            if box:
                return box
        return None

    async def _container_box(self, page):
        """The rect of the nearest ancestor of the token input that is a widget.

        Walks out from the input because the input itself is hidden and has no
        rect. The size guards are what keep a full-page wrapper from passing for
        a checkbox row: without them a click lands on blank space while every
        read still reports success.
        """
        for depth in _WIDGET_ANCESTOR_DEPTHS:
            container = page.locator(f"{TURNSTILE_SELECTORS[0]} >> xpath=ancestor::div[{depth}]")
            try:
                if await container.count() == 0:
                    continue
                box = await container.first.bounding_box(timeout=_ELEMENT_READ_MS)
            except Exception:
                logging.debug("turnstile container read raced a navigation", exc_info=True)
                continue
            if (box and box["width"] > _WIDGET_MIN_WIDTH
                    and _WIDGET_MIN_HEIGHT < box["height"] < _WIDGET_MAX_HEIGHT):
                return box
        return None

    async def _turnstile_token(self, page) -> Optional[str]:
        """Value of a Turnstile input (the solved token), or None.

        Reads the value property, not the `value` attribute. Cloudflare's own
        widget happens to write both, measured against the live widget, so the
        attribute read this replaces was not wrong there. It is wrong for a token
        that playwright-captcha injects for the paid escalation, which assigns
        `input.value` only (`appliers/applyCloudflareTurnstile.js`), and assigning
        a value never updates the attribute. The property is the element's current
        value either way, which is also what Selenium hands the Chrome engine.

        A read that races a navigation reports None, which only ever means "not
        solved yet" to the caller, so the wait loop simply looks again.
        """
        try:
            token = page.locator(TURNSTILE_SELECTORS[0])
            if await token.count() == 0:
                return None
            return await token.first.input_value(timeout=_ELEMENT_READ_MS) or None
        except Exception:
            logging.debug("turnstile token read raced a navigation", exc_info=True)
            return None

    async def _api_solve(self, page, captcha_type) -> None:
        """Solve via a paid 2captcha-compatible service (2captcha / CapSolver / ...).

        Only reached when CAPTCHA_SOLVER + CAPTCHA_API_KEY are set and free
        click-solving didn't clear the page. The service extracts the sitekey,
        solves remotely, and playwright-captcha injects the token. Always runs on
        the throwaway click page, since preparing this solver also injects init
        scripts that must not outlive the request.
        """
        try:
            from twocaptcha.async_solver import AsyncTwoCaptcha
        except Exception as e:
            raise Exception("CAPTCHA API solver unavailable (twocaptcha not installed): " + str(e))

        client = AsyncTwoCaptcha(
            apiKey=config.captcha_api_key(),
            server=config.captcha_api_server(),
        )
        async with TwoCaptchaSolver(
            framework=FrameworkType.PLAYWRIGHT,
            page=page,
            async_two_captcha_client=client,
            max_attempts=config.captcha_api_max_attempts(),
            attempt_delay=5,
        ) as solver:
            await solver.solve_captcha(captcha_container=page, captcha_type=captcha_type)

    async def _detect_settled(self, page) -> Tuple[str, bool]:
        """``_detect``, retried once when the page moves under it."""
        return await self._past_navigation(lambda: self._detect(page))

    async def _past_navigation(self, read):
        """Run a page read, looking again once if the page moved under it.

        A challenge navigates to the real page the moment it clears, and any
        title or selector read in flight then dies with "Execution context was
        destroyed". That is the challenge succeeding, not the request failing, so
        look again once the new document is in place before giving up.
        """
        for attempt in (0, 1):
            try:
                return await read()
            except Exception as e:
                if attempt:
                    raise
                logging.debug("a page read raced a navigation, retrying: %s", e)
                await asyncio.sleep(0.5)

    async def _detect(self, page) -> Tuple[str, bool]:
        """Return (kind, is_turnstile) where kind is 'denied' | 'challenge' | 'none'.

        The verdict rule is shared with the Chrome engine (pipeline.py); only the
        two looks below are this engine's. turnstile_is_a_challenge is True here
        because a bare widget is reachable: the checkbox is clicked by coordinate
        and needs no tab count from the caller.
        """
        found, is_turnstile, _reason = await pipeline.run_async({
            pipeline.Look.TITLE: lambda _arg: page.title(),
            pipeline.Look.SELECTOR: lambda selector: _present(page, selector),
        }, turnstile_is_a_challenge=True)
        if found is pipeline.Verdict.DENIED:
            return "denied", False
        return ("challenge" if found is pipeline.Verdict.CHALLENGE else "none"), is_turnstile
