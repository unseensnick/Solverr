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
        self._solver_cm = None
        self.solver = None

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
        self._solver_cm = ClickSolver(
            framework=FrameworkType.PLAYWRIGHT,
            page=self.page,
            max_attempts=config.stealth_max_attempts(),
            attempt_delay=1,
        )
        self.solver = await self._solver_cm.__aenter__()

    async def close(self):
        if self._solver_cm is not None:
            try:
                await self._solver_cm.__aexit__(None, None, None)
            except Exception:
                logging.debug("stealth solver teardown failed", exc_info=True)
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

        # Launch the Camoufox browser outside the lock (it can take seconds).
        ctx = StealthContext(_proxy_to_config(proxy))
        self._runtime.run(ctx.start(), timeout=config.stealth_start_timeout())

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
            stale = [sid for sid, c in self._sessions.items() if (now - c.last_used) > ttl]
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
            ordered = sorted(self._sessions.items(), key=lambda kv: kv[1].last_used)
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
            self._runtime.run(ctx.start(), timeout=min(timeout, config.stealth_start_timeout()))
            own_ctx = True
        try:
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
            return await asyncio.wait_for(self._navigate_and_solve(req, ctx, method), timeout=timeout)

    async def _navigate_and_solve(self, req: V1RequestBase, ctx: StealthContext,
                                  method: str) -> SolveResult:
        page = ctx.page
        nav_timeout_ms = None  # let asyncio.wait_for enforce the hard cap

        disable_media = utils.get_config_disable_media()
        if req.disableMedia is not None:
            disable_media = req.disableMedia

        block_handler = None
        if disable_media:
            async def block_handler(route):
                if route.request.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", block_handler)

        try:
            # navigate to the page
            logging.debug(f"Navigating to... {req.url}")
            resp = None
            if method == "POST":
                await page.goto("data:text/html;charset=utf-8," + build_post_html(req.url, req.postData),
                                timeout=nav_timeout_ms)
                await page.wait_for_load_state("domcontentloaded", timeout=nav_timeout_ms)
            else:
                resp = await page.goto(req.url, timeout=nav_timeout_ms)
                await page.wait_for_load_state("domcontentloaded", timeout=nav_timeout_ms)

            status = resp.status if resp else 200

            # set cookies if required, then reload (mirrors the Chrome engine)
            if req.cookies is not None and len(req.cookies) > 0:
                logging.debug("Setting cookies...")
                await ctx.context.add_cookies(req.cookies)
                if method == "POST":
                    await page.goto("data:text/html;charset=utf-8," + build_post_html(req.url, req.postData),
                                    timeout=nav_timeout_ms)
                else:
                    resp = await page.goto(req.url, timeout=nav_timeout_ms)
                    status = resp.status if resp else status
                await page.wait_for_load_state("domcontentloaded", timeout=nav_timeout_ms)

            if utils.get_config_log_html():
                logging.debug(f"Response HTML:\n{await page.content()}")

            kind, is_turnstile = await self._detect(page)
            if kind == "denied":
                raise Exception('Cloudflare has blocked this request. '
                                'Probably your IP is banned for this site, check in your web browser.')

            if kind == "challenge":
                captcha_type = (CaptchaType.CLOUDFLARE_TURNSTILE if is_turnstile
                                else CaptchaType.CLOUDFLARE_INTERSTITIAL)
                logging.info("Challenge detected. Solving with stealth engine (%s)...",
                             captcha_type.name)
                click_ok = True
                try:
                    await ctx.solver.solve_captcha(  # type: ignore[union-attr]
                        captcha_container=page,
                        captcha_type=captcha_type,
                        wait_checkbox_attempts=1,
                        wait_checkbox_delay=0.5,
                    )
                except Exception as e:
                    click_ok = False
                    logging.warning("Click-solver failed: %s", e)

                # Escalate to the paid CAPTCHA API only if configured, and only when the
                # free click-solve failed or left the page still challenged.
                if config.api_solver_enabled():
                    still_challenged = (await self._detect(page))[0] == "challenge"
                    if not click_ok or still_challenged:
                        logging.info("Escalating to paid CAPTCHA API solver (%s)...",
                                     config.captcha_provider())
                        await self._api_solve(page, captcha_type)
                elif not click_ok:
                    raise Exception("Click-solver failed and no CAPTCHA API is configured")

                logging.info("Challenge solved!")
                message = "Challenge solved!"
            else:
                try:
                    await page.wait_for_load_state("networkidle", timeout=nav_timeout_ms)
                except Exception:
                    logging.debug("networkidle wait timed out")
                logging.info("Challenge not detected!")
                message = "Challenge not detected!"

            result = SolveResult()
            result.url = page.url
            result.status = status
            result.cookies = await ctx.context.cookies()
            result.user_agent = await page.evaluate("navigator.userAgent")
            result.message = message

            if not req.returnOnlyCookies:
                result.headers = {}
                if req.waitInSeconds and req.waitInSeconds > 0:
                    logging.info("Waiting %s seconds before returning the response...", req.waitInSeconds)
                    await asyncio.sleep(req.waitInSeconds)
                result.response = await page.content()

            if req.returnScreenshot:
                result.screenshot = base64.b64encode(await page.screenshot()).decode("ascii")

            return result
        finally:
            if block_handler is not None:
                try:
                    await page.unroute("**/*", block_handler)
                except Exception:
                    logging.debug("unroute failed", exc_info=True)

    async def _api_solve(self, page, captcha_type) -> None:
        """Solve via a paid 2captcha-compatible service (2captcha / CapSolver / ...).

        Only reached when CAPTCHA_SOLVER + CAPTCHA_API_KEY are set and free
        click-solving didn't clear the page. The service extracts the sitekey,
        solves remotely, and playwright-captcha injects the token.
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
