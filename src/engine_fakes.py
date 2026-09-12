"""Browser-free fakes for driving either engine through the same assertions.

Not a test module (unittest discovers `test_*.py`), so it is imported rather
than collected. It exists because a rule that must hold for both engines is
pinned once, and the only way to pin one over both is to drive both.

Each harness takes the same `World`, a neutral description of what the browser
would find, and renders it in its own browser's dialect: Selenium cookies for
Chrome, Playwright cookies for stealth. That difference is deliberate, because
agreeing on the returned dialect is one of the rules being pinned.
"""
import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import patch

from selenium.common import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By

from dtos import V1RequestBase
from engines.chrome_engine import ChromeEngine
from engines.stealth_engine import StealthEngine

# A cookie as the site would set it, before either browser's dialect is applied.
Cookie = tuple  # (name, value, expiry_epoch_or_None)

LOADED = [("early", "1", 1893456000)]
AFTER_WAIT = LOADED + [("late", "1", None)]


@dataclass
class World:
    """What the browser would find, described once for both engines.

    `selectors` are the CSS selectors present on the page, and `challenged_for`
    is how many title reads they survive: 1 means the page looks challenged on
    the first look and clean on the next, which is a challenge that clears. 0
    means they are never there, whatever `selectors` says.
    """
    title: str = "Example"
    html: str = "<html><body>ok</body></html>"
    url: str = "https://example-site.tld/"
    user_agent: str = "UA/1.0"
    screenshot: bytes = b"\x89PNG-bytes"
    cookies_at_load: list = field(default_factory=lambda: list(LOADED))
    cookies_after_wait: list = field(default_factory=lambda: list(AFTER_WAIT))
    # Cookies belonging to some other host. A live browser holds these once a
    # session has visited more than one site, and no browser hands them to a
    # page they do not belong to, so neither engine may return them.
    foreign_cookies: list = field(default_factory=list)
    response_headers: dict = field(default_factory=lambda: {"content-type": "text/html"})
    selectors: frozenset = frozenset()
    challenged_for: int = 0
    # Title reads so far, shared by both fakes so "challenged_for" means the
    # same number of looks on either engine.
    looks: list = field(default_factory=list)
    # One entry per navigation, so the cookie-reload rule is observable.
    navigations: list = field(default_factory=list)
    # What the engine actually handed its browser, so a refused cookie shows up.
    cookies_set: list = field(default_factory=list)

    def read_title(self) -> str:
        self.looks.append(1)
        return self.title if len(self.looks) <= max(1, self.challenged_for) else "Example"

    def has(self, selector: str) -> bool:
        return selector in self.selectors and len(self.looks) <= self.challenged_for


# ---- Chrome ----------------------------------------------------------------

class _SwitchTo:
    def default_content(self):
        pass


class _HtmlElement:
    """The <html> element the Chrome engine holds to wait for staleness.

    is_enabled raises stale, which is what a real one does once the cleared
    challenge navigates to the page it was hiding. Returning True instead would
    make every challenged test wait out the redirect timeout for nothing.
    """

    def is_enabled(self):
        raise StaleElementReferenceException("the challenge navigated away")


class _SeleniumDriver:
    """The slice of the Selenium API a solve touches, challenged or not."""

    def __init__(self, world: World):
        self._world = world
        self.waited = False
        self.switch_to = _SwitchTo()
        self.current_url = world.url
        self.page_source = world.html

    @property
    def title(self):
        return self._world.read_title()

    def get(self, _url):
        self._world.navigations.append(1)

    def delete_cookie(self, _name):
        pass

    def add_cookie(self, cookie):
        # Selenium accepts a bare cookie and anchors it to the current page.
        self._world.cookies_set.append(cookie)

    def execute_script(self, _script):
        pass

    def execute_cdp_cmd(self, _cmd, _params):
        pass

    def find_element(self, by, value):
        # Selenium's presence_of_element_located calls this, not find_elements,
        # and signals absence by raising rather than returning nothing.
        if by == By.TAG_NAME and value == "html":
            return _HtmlElement()
        if by == By.CSS_SELECTOR and self._world.has(value):
            return object()
        raise NoSuchElementException(value)

    def find_elements(self, _by, selector):
        return [object()] if self._world.has(selector) else []

    def get_log(self, _kind):
        # The shape Chrome actually writes: a JSON string per entry, wrapping a
        # CDP message. An earlier Document response is included on purpose, so a
        # reader that takes the first one instead of the last is caught.
        def entry(url, headers):
            return {"message": json.dumps({"message": {
                "method": "Network.responseReceived",
                "params": {"type": "Document",
                           "response": {"url": url, "headers": headers}}}})}
        return [entry("https://example-site.tld/challenge", {"cf-mitigated": "challenge"}),
                entry(self._world.url, self._world.response_headers)]

    def get_screenshot_as_png(self):
        # Raw bytes, like Selenium's own: the base64 encoding is the kernel's job
        # so both engines hand over the same currency.
        return self._world.screenshot

    def get_cookies(self):
        # WebDriver reports the active document's cookies only, so the world's
        # foreign ones are never visible here.
        source = self._world.cookies_after_wait if self.waited else self._world.cookies_at_load
        out = []
        for name, value, expiry in source:
            cookie = {"name": name, "value": value, "domain": ".example-site.tld",
                      "path": "/", "httpOnly": False, "secure": True}
            if expiry is not None:
                cookie["expiry"] = expiry
            out.append(cookie)
        return out


class ChromeHarness:
    name = "chrome"

    def solve(self, world: World, **fields):
        driver = _SeleniumDriver(world)
        req = V1RequestBase(dict({"url": world.url, "disableMedia": False}, **fields))

        def _slept(_seconds):
            driver.waited = True

        import utils
        with patch('engines.chrome_engine.time.sleep', side_effect=_slept), \
                patch.object(utils, 'get_user_agent', return_value=world.user_agent):
            return ChromeEngine(sessions=None)._evil_logic(req, driver, "GET", 60.0)


# ---- Stealth ---------------------------------------------------------------

class _PlaywrightContext:
    def __init__(self, page):
        self._page = page

    @staticmethod
    def _cookie(name, value, expiry, domain):
        return {"name": name, "value": value, "domain": domain,
                "path": "/", "httpOnly": False, "secure": True,
                # Playwright reports -1 for a session cookie, not a missing key.
                "expires": float(expiry) if expiry is not None else -1}

    async def cookies(self, urls=None):
        # Playwright's own rule: with no url the whole context comes back, every
        # domain it has collected; with one, only the cookies that url would be
        # sent. The context outlives a request here, so the difference is the
        # difference between one site's jar and every site the session visited.
        source = (self._page.world.cookies_after_wait if self._page.waited
                  else self._page.world.cookies_at_load)
        out = [self._cookie(*c, ".example-site.tld") for c in source]
        if urls is None:
            out += [self._cookie(*c, ".other-site.tld")
                    for c in self._page.world.foreign_cookies]
        return out

    async def add_cookies(self, cookies):
        for cookie in cookies:
            # Playwright's own rule, and it refuses the whole batch on one bad
            # entry, which is what failed the request rather than the cookie.
            if not cookie.get("url") and not (cookie.get("domain") and cookie.get("path")):
                raise ValueError("Cookie should have a url or a domain/path pair")
            self._page.world.cookies_set.append(cookie)


class _PlaywrightPage:
    def __init__(self, world: World):
        self.world = world
        self.waited = False
        self.url = world.url
        self.context = _PlaywrightContext(self)
        self.main_frame = object()
        self._response_handlers = []

    async def title(self):
        return self.world.read_title()

    async def content(self):
        return self.world.html

    async def query_selector(self, selector):
        return object() if self.world.has(selector) else None

    async def goto(self, *_a, **_k):
        self.world.navigations.append(1)
        for handler in self._response_handlers:
            handler(_Response(self))

    async def wait_for_load_state(self, *_a, **_k):
        pass

    async def screenshot(self):
        return self.world.screenshot

    async def route(self, *_a, **_k):
        pass

    async def unroute(self, *_a, **_k):
        pass

    def on(self, event, handler):
        if event == "response":
            self._response_handlers.append(handler)

    def remove_listener(self, _event, handler):
        if handler in self._response_handlers:
            self._response_handlers.remove(handler)


class _Request:
    def __init__(self, page):
        self.headers = {"user-agent": page.world.user_agent}

    def is_navigation_request(self):
        return True


class _Response:
    """The main-frame navigation response both engines read headers off."""

    def __init__(self, page):
        self.request = _Request(page)
        self.frame = page.main_frame
        self.headers = dict(page.world.response_headers)


class _StealthCtx:
    def __init__(self, world: World):
        self.page = _PlaywrightPage(world)
        self.context = self.page.context
        self.user_agent = world.user_agent
        self.lock = asyncio.Lock()


class StealthHarness:
    name = "stealth"

    def solve(self, world: World, **fields):
        ctx = _StealthCtx(world)
        req = V1RequestBase(dict({"url": world.url, "disableMedia": False}, **fields))
        # __new__ rather than __init__: the constructor starts the background
        # event loop thread, and nothing on this path touches it.
        engine = StealthEngine.__new__(StealthEngine)
        real_sleep = asyncio.sleep

        async def _slept(seconds, *a, **k):
            if seconds:
                ctx.page.waited = True
            return await real_sleep(0)

        async def run():
            with patch('asyncio.sleep', _slept):
                return await engine._navigate_and_solve(req, ctx, "GET", 60.0)

        return asyncio.run(run())


HARNESSES = (ChromeHarness(), StealthHarness())
