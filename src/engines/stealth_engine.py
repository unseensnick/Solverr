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
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
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

    Anchored to ``url`` when the caller did not say where a cookie belongs.
    Playwright refuses a cookie carrying neither a url nor a domain/path pair,
    and refuses the whole batch, so `{"name": "a", "value": "1"}` (the shape the
    README documents and the one FlareSolverr clients send) failed the entire
    request on this engine while working on the Chrome one. Selenium's add_cookie
    defaults such a cookie to the page it is on, so anchoring to the request URL
    is the same behaviour, not a new one. A domain without a path gets Selenium's
    default of "/" for the same reason.
    """
    converted = []
    for cookie in cookies:
        translated = {k: v for k, v in cookie.items() if k in _PLAYWRIGHT_COOKIE_KEYS}
        if "expires" not in translated and cookie.get("expiry") is not None:
            translated["expires"] = float(cookie["expiry"])
        if not translated.get("url"):
            if translated.get("domain"):
                translated.setdefault("path", "/")
            else:
                translated["url"] = url
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
        self.created_at = datetime.now()
        self.last_used = self.created_at
        self.lock = asyncio.Lock()
        self._ip = None
        self.browser = None
        self.context = None
        self.page = None
        self.user_agent = ""

    def lifetime(self) -> timedelta:
        return datetime.now() - self.created_at

    def idle(self) -> timedelta:
        return datetime.now() - self.last_used

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

    # ---- session registry (controller-facing) -------------------------------
    #
    # Every rule below lives in sessions.SessionStore, shared with the Chrome
    # engine: idempotent creation, the in-use mark that keeps the reaper off a
    # live browser, TTL expiry, idle reaping and the cap. This engine supplies
    # only how to start and close what a session holds.

    def _start_context(self, proxy: Optional[dict]) -> "StealthContext":
        ctx = StealthContext(geo.proxy_to_config(proxy))
        try:
            self._runtime.run(ctx.start(), timeout=config.stealth_start_timeout())
        except Exception:
            # start() may already have launched the browser before failing.
            self._close_context(ctx)
            raise
        return ctx

    def _close_context(self, ctx: "StealthContext") -> None:
        self._runtime.run(ctx.close(), timeout=60)

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
        own_ctx = False
        # get() hands the session over already marked in use, so the reaper and
        # the cap cannot close the browser under this request. Released in the
        # finally below, exactly as the Chrome engine does it.
        in_use = None
        if req.session:
            ttl = timedelta(minutes=req.session_ttl_minutes) if req.session_ttl_minutes else None
            session, _ = self._sessions.get(req.session, ttl, req.proxy)
            in_use = session
            ctx = session.payload
        else:
            ctx = StealthContext(geo.proxy_to_config(req.proxy))
            # Owned before start(): a launch that fails or times out has usually
            # already spawned the browser, and only the finally below closes it.
            own_ctx = True
        try:
            if own_ctx:
                self._runtime.run(ctx.start(), timeout=min(timeout, config.stealth_start_timeout()))
            return self._runtime.run(self._do_solve(req, ctx, method, timeout), timeout=timeout + 5)
        except FuturesTimeout:
            raise Exception(f'Error solving the challenge. Timeout after {timeout} seconds.')
        except Exception as e:
            raise Exception('Error solving the challenge. ' + str(e).replace('\n', '\\n'))
        finally:
            if in_use is not None:
                self._sessions.end_use(in_use)
            if own_ctx:
                try:
                    self._runtime.run(ctx.close(), timeout=60)
                except Exception:
                    logging.debug("stealth ctx teardown failed", exc_info=True)

    async def _do_solve(self, req: V1RequestBase, ctx: StealthContext, method: str,
                        timeout: float) -> SolveResult:
        async with ctx.lock:
            return await asyncio.wait_for(self._navigate_and_solve(req, ctx, method, timeout), timeout=timeout)

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
                    if route.request.resource_type in ("image", "media", "font"):
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
                raise Exception('Cloudflare has blocked this request. '
                                'Probably your IP is banned for this site, check in your web browser.')

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
                # Both kinds are handled on the context's own page: an interstitial
                # clears itself, and a widget is clicked by coordinate, so neither
                # needs the solver's init scripts. Only the paid escalation below
                # does, and it moves to the throwaway page for them.
                solved = await self._wait_until_cleared(None, page, captcha_type, deadline)

                # Escalate to the paid CAPTCHA API only if configured and still stuck.
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
                for state in ("domcontentloaded", "networkidle"):
                    try:
                        await page.wait_for_load_state(state, timeout=_NETWORKIDLE_MS)
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

    async def _wait_until_cleared(self, solver, page, captcha_type, deadline) -> bool:
        """Wait for the Cloudflare challenge to clear, up to ``deadline``.

        Non-interactive interstitials solve themselves after a few seconds of JS,
        so for those this just polls for the challenge to disappear. An interactive
        Turnstile needs a click, so its checkbox is clicked and re-clicked while it
        stays unsolved. A ``solver`` is passed only by the paid escalation, which
        nudges playwright-captcha instead.
        """
        loop = asyncio.get_running_loop()
        is_turnstile = captcha_type == CaptchaType.CLOUDFLARE_TURNSTILE
        last_click = 0.0
        while True:
            kind = (await self._detect_settled(page))[0]
            if kind == "denied":
                raise Exception('Cloudflare has blocked this request. '
                                'Probably your IP is banned for this site, check in your web browser.')
            if kind == "challenge":
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    logging.debug("challenge still present (title=%r, url=%s)",
                                  await page.title(), page.url)
                if solver is not None:
                    try:
                        await solver.solve_captcha(
                            captcha_container=page,
                            captcha_type=captcha_type,
                            wait_checkbox_attempts=1,
                            wait_checkbox_delay=0.5,
                        )
                    except Exception as e:
                        logging.debug("click-solve nudge: %s", e)
                elif is_turnstile:
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
        """
        for sel in INTERSTITIAL_SELECTORS:
            if await page.query_selector(sel):
                return True
        return False

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
        """``_detect``, retried once when the page moves under it.

        A challenge navigates to the real page the moment it clears, and any
        title/selector read in flight then dies with "Execution context was
        destroyed". That is the challenge succeeding, not the request failing, so
        look again once the new document is in place before giving up.
        """
        for attempt in (0, 1):
            try:
                return await self._detect(page)
            except Exception as e:
                if attempt:
                    raise
                logging.debug("detection raced a navigation, retrying: %s", e)
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
