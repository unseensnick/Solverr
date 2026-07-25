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
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from uuid import uuid1

from invisible_playwright.async_api import InvisiblePlaywright
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType, TwoCaptchaSolver

import config
import utils
from async_runtime import get_runtime
from detection import (ACCESS_DENIED_TITLES, ACCESS_DENIED_SELECTORS,
                       CHALLENGE_TITLES, CHALLENGE_SELECTORS, TURNSTILE_SELECTORS)
from dtos import V1RequestBase
from engines.base import Engine, SolveResult
from postform import build_post_html

# Best-effort settle waits are bounded; the hard navigation cap comes from the
# request's maxTimeout via asyncio.wait_for in _do_solve.
_NETWORKIDLE_MS = 5000

# A returned document is base64-encoded on top of the raw bytes and copied again
# by the JSON response, so cap what we are willing to pull into memory.
_MAX_PDF_BYTES = 32 * 1024 * 1024

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


def _to_playwright_cookies(cookies: list) -> list:
    """Client-supplied cookies to Playwright's shape, accepting either dialect."""
    converted = []
    for cookie in cookies:
        translated = {k: v for k, v in cookie.items() if k in _PLAYWRIGHT_COOKIE_KEYS}
        if "expires" not in translated and cookie.get("expiry") is not None:
            translated["expires"] = float(cookie["expiry"])
        converted.append(translated)
    return converted


def _proxy_to_config(proxy: Optional[dict]) -> Optional[dict]:
    """Convert a FlareSolverr proxy dict ({url, username, password}) to the
    Playwright/Camoufox shape ({server, username, password})."""
    if not proxy or 'url' not in proxy:
        return None
    cfg = {"server": proxy['url']}
    if proxy.get('username'):
        cfg['username'] = proxy['username']
    if proxy.get('password'):
        cfg['password'] = proxy['password']
    return cfg


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
        self._ip = InvisiblePlaywright(
            headless=config.stealth_headless(),
            proxy=self.proxy_config,
            humanize=True,
            locale="auto",
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
        self._sessions = {}  # session_id -> StealthContext
        self._sessions_lock = threading.Lock()

    # ---- session registry (controller-facing) -------------------------------

    def session_ids(self) -> List[str]:
        with self._sessions_lock:
            return list(self._sessions.keys())

    def exists(self, session_id: str) -> bool:
        with self._sessions_lock:
            return session_id in self._sessions

    def create_session(self, session_id: Optional[str] = None, proxy: Optional[dict] = None,
                       force_new: bool = False) -> Tuple[str, bool]:
        session_id = session_id or str(uuid1())
        if force_new:
            self.destroy_session(session_id)

        with self._sessions_lock:
            if session_id in self._sessions:
                return session_id, False

        # Launch the browser outside the lock (it can take seconds).
        ctx = StealthContext(_proxy_to_config(proxy))
        try:
            self._runtime.run(ctx.start(), timeout=config.stealth_start_timeout())
        except Exception:
            # start() may already have launched the browser before failing.
            self._teardown(ctx)
            raise

        with self._sessions_lock:
            race = self._sessions.get(session_id)
            if race is None:
                self._sessions[session_id] = ctx
        if race is not None:
            self._teardown(ctx)
            return session_id, False
        return session_id, True

    def destroy_session(self, session_id: str) -> bool:
        with self._sessions_lock:
            ctx = self._sessions.pop(session_id, None)
        if ctx is None:
            return False
        self._teardown(ctx)
        return True

    def touch(self, session_id: str) -> None:
        with self._sessions_lock:
            ctx = self._sessions.get(session_id)
        if ctx is not None:
            ctx.last_used = datetime.now()

    def reap_idle(self, ttl: timedelta) -> List[str]:
        if ttl is None or ttl.total_seconds() <= 0:
            return []
        now = datetime.now()
        with self._sessions_lock:
            # A context solving right now is not idle, whatever its timestamp says.
            stale = [sid for sid, c in self._sessions.items()
                     if (now - c.last_used) > ttl and not c.lock.locked()]
            popped = [(sid, self._sessions.pop(sid)) for sid in stale]
        for _, ctx in popped:
            self._teardown(ctx)
        return [sid for sid, _ in popped]

    def enforce_cap(self, max_sessions: int) -> List[str]:
        if max_sessions is None or max_sessions <= 0:
            return []
        with self._sessions_lock:
            if len(self._sessions) <= max_sessions:
                return []
            # Never evict a context mid-solve: the request would die with
            # "Target page, context or browser has been closed". The cap is
            # best-effort, so under full pressure we just evict fewer.
            ordered = sorted((kv for kv in self._sessions.items() if not kv[1].lock.locked()),
                             key=lambda kv: kv[1].last_used)
            to_remove = ordered[: len(self._sessions) - max_sessions]
            for sid, _ in to_remove:
                self._sessions.pop(sid, None)
        for _, ctx in to_remove:
            self._teardown(ctx)
        return [sid for sid, _ in to_remove]

    def _teardown(self, ctx: "StealthContext") -> None:
        try:
            self._runtime.run(ctx.close(), timeout=60)
        except Exception:
            logging.debug("stealth session teardown failed", exc_info=True)

    def _get_session(self, session_id: str, ttl: Optional[timedelta]) -> Tuple[StealthContext, bool]:
        fresh = False
        with self._sessions_lock:
            ctx = self._sessions.get(session_id)
        if ctx is not None and ttl is not None and ctx.lifetime() > ttl:
            logging.debug(f"stealth session expired, recreating (session_id={session_id})")
            self.destroy_session(session_id)
            ctx = None
        # (Re)create, tolerating a reaper/cap eviction racing between calls.
        for _ in range(2):
            if ctx is not None:
                break
            self.create_session(session_id)
            fresh = True
            with self._sessions_lock:
                ctx = self._sessions.get(session_id)
        if ctx is None:
            raise Exception("Failed to create stealth session")
        ctx.last_used = datetime.now()
        return ctx, fresh

    # ---- solving ------------------------------------------------------------

    def solve(self, req: V1RequestBase, method: str, timeout: float) -> SolveResult:
        own_ctx = False
        if req.session:
            ttl = timedelta(minutes=req.session_ttl_minutes) if req.session_ttl_minutes else None
            ctx, _ = self._get_session(req.session, ttl)
        else:
            ctx = StealthContext(_proxy_to_config(req.proxy))
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
            await navigate(page)

            # set cookies if required, then reload (mirrors the Chrome engine)
            if req.cookies is not None and len(req.cookies) > 0:
                logging.debug("Setting cookies...")
                await ctx.context.add_cookies(_to_playwright_cookies(req.cookies))
                await navigate(page)

            if utils.get_config_log_html():
                logging.debug(f"Response HTML:\n{await page.content()}")

            kind, is_turnstile = await self._detect(page)
            if kind == "none":
                # An async challenge or widget (e.g. a Turnstile injected via api.js
                # after domcontentloaded) may not be in the DOM yet; settle and recheck.
                try:
                    await page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_MS)
                except Exception:
                    logging.debug("networkidle wait timed out")
                kind, is_turnstile = await self._detect(page)

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
                deadline = started + max(1.0, timeout - 3)
                # An interstitial clears itself and is the case that must stay
                # unpatched; only a widget that needs clicking gets a solver.
                solver = await open_click_page() if is_turnstile else None
                solved = await self._wait_until_cleared(solver, page, captcha_type, deadline)

                # Escalate to the paid CAPTCHA API only if configured and still stuck.
                if not solved and config.api_solver_enabled():
                    logging.info("Escalating to paid CAPTCHA API solver (%s)...",
                                 config.captcha_provider())
                    await open_click_page()
                    await self._api_solve(page, captcha_type)
                    solved = (await self._detect(page))[0] != "challenge"

                if not solved:
                    raise Exception("Challenge still present after solving attempts")
                # Let the post-challenge redirect to the real page settle before we
                # read the content.
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=_NETWORKIDLE_MS)
                except Exception:
                    logging.debug("post-solve settle timed out")
                logging.info("Challenge solved!")
                message = "Challenge solved!"
            else:
                logging.info("Challenge not detected!")
                message = "Challenge not detected!"

            result = SolveResult()
            result.url = page.url
            # FlareSolverr contract: solution.status is 200 on a fetched/solved page,
            # and clients (e.g. the reader app) reject non-2xx. The Chrome engine also
            # hardcodes 200; report 200 here so a Cloudflare 403 challenge page (or an
            # upstream 403) doesn't get surfaced as a solve failure. A real block is
            # already raised as an error by the "denied" detection above.
            result.status = 200
            result.cookies = _to_client_cookies(await ctx.context.cookies())
            result.user_agent = ctx.user_agent
            result.message = message
            # Parity with the Chrome engine: return the Turnstile token when a
            # standalone widget is present.
            if is_turnstile:
                result.turnstile_token = await self._turnstile_token(page)

            if not req.returnOnlyCookies:
                result.headers = {}
                if req.waitInSeconds and req.waitInSeconds > 0:
                    logging.info("Waiting %s seconds before returning the response...", req.waitInSeconds)
                    await asyncio.sleep(req.waitInSeconds)
                pdf = await self._pdf_body(page, main_response)
                if pdf is not None:
                    result.response = pdf
                    result.content_type = "application/pdf"
                else:
                    result.response = await page.content()

            if req.returnScreenshot:
                result.screenshot = base64.b64encode(await page.screenshot()).decode("ascii")

            return result
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
        so with no ``solver`` this just polls for the challenge to disappear.
        Interactive Turnstile/checkbox challenges need a click, so when a solver is
        given each pass also nudges it (a harmless "iframes not found" when there
        is no checkbox to click).
        """
        loop = asyncio.get_running_loop()
        is_turnstile = captcha_type == CaptchaType.CLOUDFLARE_TURNSTILE
        while True:
            kind = (await self._detect(page))[0]
            if kind == "denied":
                raise Exception('Cloudflare has blocked this request. '
                                'Probably your IP is banned for this site, check in your web browser.')
            if kind != "challenge":
                return True
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

            # A standalone Turnstile widget stays in the DOM after solving, so
            # _detect keeps seeing it. The filled token is the proof it cleared:
            # the click-solver also returns without raising when it simply found
            # no widget to click, so its silence means nothing on its own.
            if is_turnstile and await self._turnstile_token(page):
                return True
            if (await self._detect(page))[0] != "challenge":
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(1.5)

    async def _turnstile_token(self, page) -> Optional[str]:
        """Value of a standalone Turnstile input (the solved token), or None.
        Uses get_attribute, which works even under a challenge page's CSP."""
        el = await page.query_selector(TURNSTILE_SELECTORS[0])
        return (await el.get_attribute("value")) if el else None

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

    async def _detect(self, page) -> Tuple[str, bool]:
        """Return (kind, is_turnstile) where kind is 'denied' | 'challenge' | 'none'.

        Uses the same title/selector lists as the Chrome engine so detection
        coverage (Cloudflare interstitial, Turnstile, DDoS-Guard, custom) is identical.
        """
        title = await page.title()

        for t in ACCESS_DENIED_TITLES:
            if title.startswith(t):
                return "denied", False
        for sel in ACCESS_DENIED_SELECTORS:
            if await page.query_selector(sel):
                return "denied", False

        is_turnstile = False
        for sel in TURNSTILE_SELECTORS:
            if await page.query_selector(sel):
                is_turnstile = True
                break

        challenge = is_turnstile
        if not challenge:
            for t in CHALLENGE_TITLES:
                if t.lower() == title.lower():
                    challenge = True
                    break
        if not challenge:
            for sel in CHALLENGE_SELECTORS:
                if await page.query_selector(sel):
                    challenge = True
                    break

        return ("challenge" if challenge else "none"), is_turnstile
