"""Chrome engine: Selenium + vendored undetected_chromedriver.

FlareSolverr's original solving path, behind the Engine interface. It stays the
default engine because it empirically clears the hardest sites and already
supports sessions, POST, cookie injection and screenshots.

The clearing core below (`_evil_logic` and the Turnstile helpers) is still
upstream's and still syncs from it. What wraps it is ours: the session handover
and its lock, the browser identity, response headers, the shared spine calls,
and the share of `maxTimeout` this engine gets. Those have diverged from
upstream deliberately, and each divergence is recorded in
docs/dev/upstream-sync.md.
"""
import json
import logging
import time
from datetime import timedelta
from typing import Optional

from func_timeout import FunctionTimedOut, func_timeout
from selenium.common import (NoSuchElementException, StaleElementReferenceException,
                             TimeoutException)
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.expected_conditions import (
    presence_of_element_located, staleness_of, title_is)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.wait import WebDriverWait

import assembly
import budget
import config
import geo
import pipeline
import utils
from detection import CHALLENGE_TITLES, CHALLENGE_SELECTORS, TURNSTILE_SELECTORS
from dtos import V1RequestBase
from engines.base import Engine, SolveResult
from postform import build_post_html

# One CSS list rather than a selector-by-selector sweep, so the wait below is
# bounded once no matter how long TURNSTILE_SELECTORS grows.
_TURNSTILE_SELECTOR = ", ".join(TURNSTILE_SELECTORS)

# How long to let a Turnstile widget appear before deciding the page has none.
# driver.get() returns at readyState complete, but a widget injected by
# Cloudflare's api.js lands after that: measured 2026-08-25 over four samples,
# the token input showed up 0.01s to 0.92s after get() returned. Five seconds is
# the same grace the stealth engine gives it through its networkidle settle
# (_NETWORKIDLE_MS), and only a request that asked for tabs_till_verify can wait
# it out on a page that turns out to have no widget at all.
_WIDGET_RENDER_SECONDS = 5

# What disableMedia blocks: images, stylesheets and fonts, which is what the
# README promises and what the stealth engine blocks by resource type. Chrome
# has no resource-type filter, so the same rule is spelled as URL patterns.
_MEDIA_BLOCK_URLS = [
    # Images
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.bmp", "*.svg", "*.ico",
    "*.PNG", "*.JPG", "*.JPEG", "*.GIF", "*.WEBP", "*.BMP", "*.SVG", "*.ICO",
    "*.tiff", "*.tif", "*.jpe", "*.apng", "*.avif", "*.heic", "*.heif",
    "*.TIFF", "*.TIF", "*.JPE", "*.APNG", "*.AVIF", "*.HEIC", "*.HEIF",
    # Stylesheets
    "*.css",
    "*.CSS",
    # Fonts
    "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot",
    "*.WOFF", "*.WOFF2", "*.TTF", "*.OTF", "*.EOT"
]

class ChromeEngine(Engine):
    """Solve challenges with a real Chromium driven by undetected_chromedriver."""

    name = "chrome"
    # A checkbox lives in a closed shadow root, so the only way in is the tab
    # order, and only the caller knows how many stops away it is.
    presses_checkbox_unaided = False

    def __init__(self, sessions):
        # Shared SessionStore; a Chrome session's payload is a live WebDriver.
        self._sessions = sessions

    def solve(self, req: V1RequestBase, method: str, timeout: float) -> SolveResult:
        # The share starts here, not once the browser is up: launching one takes
        # seconds, and they used to be spent outside the budget entirely.
        started = time.monotonic()
        driver = None
        # get() hands the session over already marked in use, so nothing can quit
        # the browser under this request. Released in the finally below, which
        # runs in this thread and so survives func_timeout stopping the worker.
        in_use = None
        # The session lock this request holds, released in the finally below.
        locked = None
        # The proxy this browser actually exits through. For a session that is
        # the proxy it was built with, not the one on this request: the /v1
        # contract ignores a request proxy when a session is named, so taking it
        # from the request would pin the timezone to an exit the traffic never
        # uses, and say a different country than the browser's own language.
        browser_proxy = req.proxy
        try:
            if req.session:
                session_id = req.session
                ttl = timedelta(minutes=req.session_ttl_minutes) if req.session_ttl_minutes else None
                session, fresh = self._sessions.get(session_id, ttl, req.proxy)
                in_use = session
                browser_proxy = session.proxy
                # One request at a time on this browser. Waiting counts against
                # the share, like the launch does, so a queue cannot push the
                # request past the budget the caller asked for.
                if not session.lock.acquire(timeout=budget.remaining_share(started, timeout)):
                    raise Exception("Timed out waiting for session '%s' to be free." % session_id)
                locked = session.lock

                if fresh:
                    logging.debug(f"new session created to perform the request (session_id={session_id})")
                else:
                    logging.debug(f"existing session is used to perform the request (session_id={session_id}, "
                                  f"lifetime={str(session.lifetime())}, ttl={str(ttl)})")

                driver = session.payload
            else:
                driver = utils.get_webdriver(req.proxy)
                logging.debug('New instance of webdriver has been created to perform the request')
            _apply_timezone(driver, browser_proxy)
            left = budget.remaining_share(started, timeout)
            return func_timeout(left, self._evil_logic, (req, driver, method, left))
        except FunctionTimedOut:
            raise Exception(f'Error solving the challenge. Timeout after {timeout} seconds.')
        except Exception as e:
            raise Exception('Error solving the challenge. ' + str(e).replace('\n', '\\n'))
        finally:
            if driver is not None and req.session and config.response_headers():
                # A session's browser keeps its network log between requests and
                # only a read empties it. The read that reports the headers is
                # skipped under returnOnlyCookies and never reached when a solve
                # fails, so without this the log grows for the session's life.
                _drain_performance_log(driver)
            if locked is not None:
                locked.release()
            if in_use is not None:
                self._sessions.end_use(in_use)
            if not req.session and driver is not None:
                if utils.PLATFORM_VERSION == "nt":
                    driver.close()
                driver.quit()
                logging.debug('A used instance of webdriver has been destroyed')

    def _evil_logic(self, req: V1RequestBase, driver: WebDriver, method: str,
                    timeout: float) -> SolveResult:
        message = ""
        started = time.monotonic()

        # optionally block resources like images/css/fonts using CDP
        disable_media = utils.get_config_disable_media()
        if req.disableMedia is not None:
            disable_media = req.disableMedia
        # Sent on every request, with an empty list when nothing is to be
        # blocked: a session's driver keeps this CDP state, so a block set once
        # went on blocking for every later request on that session, including
        # ones that asked for media. The stealth engine drops its own routing at
        # the end of each request for the same reason.
        block_urls = _MEDIA_BLOCK_URLS if disable_media else []
        try:
            logging.debug("Network.setBlockedURLs: %s", block_urls)
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": block_urls})
        except Exception:
            # if CDP commands are not available or fail, ignore and continue
            logging.debug("Network.setBlockedURLs failed or unsupported on this webdriver")

        # navigate to the page
        logging.debug(f"Navigating to... {req.url}")
        turnstile_token = None

        def navigate(_arg):
            if method == "POST":
                _post_request(req, driver)
            else:
                driver.get(req.url)

        def set_cookies(cookies):
            for cookie in cookies:
                driver.delete_cookie(cookie['name'])
                driver.add_cookie(cookie)

        # The navigate-then-cookies-then-navigate order is shared with the
        # stealth engine (pipeline.py); only the two steps above are Chrome's.
        pipeline.run({pipeline.Step.NAVIGATE: navigate,
                      pipeline.Step.SET_COOKIES: set_cookies},
                     kernel=pipeline.approach, req=req)

        # After the cookie reload, not before it: the reload replaces the document,
        # so a token resolved first described a page that no longer exists by the
        # time it was returned. POST is left out, as it was before.
        if method != "POST" and req.tabs_till_verify is not None:
            deadline = budget.solve_deadline(started, timeout)
            turnstile_token = _resolve_turnstile_captcha(driver, req.tabs_till_verify, deadline)

        # wait for the page
        if utils.get_config_log_html():
            logging.debug(f"Response HTML:\n{driver.page_source}")
        html_element = driver.find_element(By.TAG_NAME, "html")

        # The verdict rule is shared with the stealth engine (pipeline.py); only
        # the two looks below are Chrome's. turnstile_is_a_challenge is False
        # here: without a tabs_till_verify count there is no way to reach the
        # checkbox, so treating a bare widget as a challenge would spend the
        # whole budget in a wait loop that cannot win.
        found, _is_turnstile, reason = pipeline.run({
            pipeline.Look.TITLE: lambda _arg: driver.title,
            pipeline.Look.SELECTOR: lambda selector: bool(
                driver.find_elements(By.CSS_SELECTOR, selector)),
        }, turnstile_is_a_challenge=False)

        if found is pipeline.Verdict.DENIED:
            raise Exception(pipeline.BLOCKED_MESSAGE)
        challenge_found = found is pipeline.Verdict.CHALLENGE
        if challenge_found:
            logging.info("Challenge detected. Found: " + str(reason))

        # Read once per request rather than per wait: the whole solve runs under
        # func_timeout(maxTimeout), so a larger value only slows the retry
        # cadence, it cannot outrun the request's share of the budget.
        wait_timeout = config.browser_wait_timeout()
        attempt = 0
        if challenge_found:
            while True:
                try:
                    attempt = attempt + 1
                    # wait until the title changes
                    for title in CHALLENGE_TITLES:
                        logging.debug("Waiting for title (attempt " + str(attempt) + "): " + title)
                        WebDriverWait(driver, wait_timeout).until_not(title_is(title))

                    # then wait until all the selectors disappear
                    for selector in CHALLENGE_SELECTORS:
                        logging.debug("Waiting for selector (attempt " + str(attempt) + "): " + selector)
                        WebDriverWait(driver, wait_timeout).until_not(
                            presence_of_element_located((By.CSS_SELECTOR, selector)))

                    # all elements not found
                    break

                except TimeoutException:
                    logging.debug("Timeout waiting for selector")

                    click_verify(driver)

                    # update the html (cloudflare reloads the page every 5 s)
                    html_element = driver.find_element(By.TAG_NAME, "html")

            # waits until cloudflare redirection ends
            logging.debug("Waiting for redirect")
            # noinspection PyBroadException
            try:
                WebDriverWait(driver, wait_timeout).until(staleness_of(html_element))
            except Exception:
                logging.debug("Timeout waiting for redirect")

            logging.info("Challenge solved!")
            message = "Challenge solved!"
        else:
            logging.info("Challenge not detected!")
            message = "Challenge not detected!"

        # Order and field rules live in assembly.py, shared with the stealth
        # engine. Only the reads below are Chrome's, and none of them is a rule.
        return assembly.run(req, message, {
            assembly.Read.URL: lambda: driver.current_url,
            assembly.Read.USER_AGENT: lambda: utils.get_user_agent(driver),
            assembly.Read.TOKEN: lambda: turnstile_token,
            assembly.Read.HEADERS: lambda: _response_headers(driver, driver.current_url),
            assembly.Read.WAIT: lambda: time.sleep(req.waitInSeconds),
            assembly.Read.BODY: lambda: (driver.page_source, None),
            assembly.Read.SCREENSHOT: lambda: driver.get_screenshot_as_png(),
            assembly.Read.COOKIES: lambda: driver.get_cookies(),
        })


def _response_headers(driver: WebDriver, page_url: str) -> dict:
    """The returned page's response headers, or {} when the feature is off.

    Selenium has no API for these, so the browser is asked at launch to log
    network events (see get_webdriver) and the document responses are picked out
    of that log here. An iframe is a Document too, and a Cloudflare challenge
    leaves one behind, so the entry has to be matched against the URL the caller
    is getting back rather than taken as whichever came last. The stealth engine
    reports its main-frame navigation response for the same reason.

    Falls back to the last document entry when nothing matches, which is what a
    redirect chain that ends on a URL the log never named looks like.
    """
    if not config.response_headers():
        return {}
    entries = _drain_performance_log(driver)
    if entries is None:
        return {}

    page, last = {}, {}
    for entry in entries:
        try:
            message = json.loads(entry['message'])['message']
            if message.get('method') != 'Network.responseReceived':
                continue
            params = message.get('params') or {}
            if params.get('type') != 'Document':
                continue
            response = params.get('response') or {}
            headers = response.get('headers') or {}
            if not headers:
                continue
            last = headers
            if _same_document(response.get('url'), page_url):
                page = headers
        except Exception:
            logging.debug("could not read a performance log entry", exc_info=True)
    return page or last


def _same_document(logged_url, page_url: str) -> bool:
    """Whether a logged response URL is the document the caller is getting.

    Compared without the fragment, which is never sent to the server and so
    never appears on the response, but does appear on driver.current_url.
    """
    if not logged_url or not page_url:
        return False
    return logged_url.split('#', 1)[0] == page_url.split('#', 1)[0]


def _drain_performance_log(driver: WebDriver):
    """Read and so empty the browser's network log, or None when it has none.

    Reading is what empties it, and a session's driver lives across requests, so
    every request has to read it even when its headers are not going to be
    reported: under returnOnlyCookies, or after a solve that failed, the entries
    used to pile up in the browser for the life of the session.
    """
    try:
        return driver.get_log('performance')
    except Exception:
        logging.debug("performance log unavailable, reporting no response headers", exc_info=True)
        return None


def _apply_timezone(driver: WebDriver, proxy: dict = None) -> None:
    """Put Chrome in the same timezone the stealth engine would use.

    Chrome otherwise reports the container's timezone whatever the traffic exits
    through, so the same request answered by the two engines disagreed about
    where the browser was. Emulation.setTimezoneOverride changes the browser's
    own ICU clock rather than patching Intl in the page, so nothing in the
    document looks rewritten.

    Best-effort by design: a browser in the wrong timezone still solves, and this
    must never be the reason a request fails.
    """
    zone = geo.browser_timezone(geo.proxy_to_config(proxy))
    try:
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": zone})
    except Exception:
        logging.debug("could not set the browser timezone to %s", zone, exc_info=True)


def click_verify(driver: WebDriver, num_tabs: int = 1):
    try:
        logging.debug("Try to find the Cloudflare verify checkbox...")
        actions = ActionChains(driver)
        actions.pause(5)
        for _ in range(num_tabs):
            actions.send_keys(Keys.TAB).pause(0.1)
        actions.pause(1)
        actions.send_keys(Keys.SPACE).perform()

        logging.debug(f"Cloudflare verify checkbox clicked after {num_tabs} tabs!")
    except Exception:
        logging.debug("Cloudflare verify checkbox not found on the page.")
    finally:
        driver.switch_to.default_content()

    try:
        logging.debug("Try to find the Cloudflare 'Verify you are human' button...")
        button = driver.find_element(
            by=By.XPATH,
            value="//input[@type='button' and @value='Verify you are human']",
        )
        if button:
            actions = ActionChains(driver)
            actions.move_to_element_with_offset(button, 5, 7)
            actions.click(button)
            actions.perform()
            logging.debug("The Cloudflare 'Verify you are human' button found and clicked!")
    except Exception:
        logging.debug("The Cloudflare 'Verify you are human' button not found on the page.")

    time.sleep(2)


def _has_turnstile_widget(driver: WebDriver) -> bool:
    """Whether a Turnstile widget is on the page, waiting briefly for a late one.

    The read used to happen the instant driver.get() returned, which is before
    Cloudflare's api.js has injected the token input, so a widget that renders
    asynchronously was reported as no widget at all and the request answered
    "Challenge not detected!" with an empty token. WebDriverWait evaluates once
    before it sleeps, so a widget already in the DOM costs nothing here.
    """
    try:
        WebDriverWait(driver, _WIDGET_RENDER_SECONDS).until(
            presence_of_element_located((By.CSS_SELECTOR, _TURNSTILE_SELECTOR)))
        return True
    except TimeoutException:
        return False


def _turnstile_token_value(driver: WebDriver) -> Optional[str]:
    """Current value of the Turnstile token input, or None if it cannot be read.

    Located fresh on every call. Holding the element across the retry loop let a
    re-render raise StaleElementReferenceException out of the whole request,
    where a read that raced one only ever means "not solved yet" to the caller.
    """
    try:
        element = driver.find_element(By.CSS_SELECTOR, _TURNSTILE_SELECTOR)
        return element.get_attribute("value") or None
    except (NoSuchElementException, StaleElementReferenceException):
        logging.debug("turnstile token read raced a navigation", exc_info=True)
        return None


def _get_turnstile_token(driver: WebDriver, tabs: int, deadline: float) -> Optional[str]:
    """Press the checkbox until the token changes, or the budget runs out.

    Returns None on the deadline rather than raising: an unsolved widget is a
    result the controller can retry on the other engine, while an exception here
    becomes a generic solve error. The loop was previously unbounded, which only
    stayed survivable because a widget that rendered late was never found at all.

    A pass is only started when the previous one's duration still fits, because
    one costs about nine seconds (click_verify's own pauses) and checking only at
    the top overran the deadline by a full pass. Measured against a live widget:
    that overrun was enough to trip func_timeout and turn "no token" into a
    timeout error. The first pass always runs, so a tiny budget still tries once.
    """
    current_value = _turnstile_token_value(driver)
    pass_seconds = 0.0
    while time.monotonic() + pass_seconds < deadline:
        pass_started = time.monotonic()
        click_verify(driver, num_tabs=tabs)
        turnstile_token = _turnstile_token_value(driver)
        if turnstile_token:
            if turnstile_token != current_value:
                logging.info(f"Turnstile token: {turnstile_token}")
                return turnstile_token
        logging.debug(f"Failed to extract token possibly click failed")

        # Reset focus. Reuses one id and removes the previous helper first:
        # prepending a fresh button per failed attempt stacked them up, and each
        # one is another stop in the tab order, so tabs_till_verify stopped
        # reaching the checkbox after the first retry. Kept nearly transparent
        # and click-through so it cannot cover the page it sits on top of.
        driver.execute_script("""
            let old = document.getElementById('__focus_helper');
            if (old) old.remove();

            let el = document.createElement('button');
            el.id = '__focus_helper';
            el.style.position = 'fixed';
            el.style.top = '0';
            el.style.left = '0';
            el.style.opacity = '0.01';
            el.style.pointerEvents = 'none';
            document.body.prepend(el);
            el.focus();
        """)
        time.sleep(1)
        pass_seconds = time.monotonic() - pass_started
    logging.warning("Turnstile checkbox did not produce a token within the request budget")
    return None


def _resolve_turnstile_captcha(driver: WebDriver, tabs: int, deadline: float) -> Optional[str]:
    """The Turnstile token for a page that carries a widget, or None."""
    if not _has_turnstile_widget(driver):
        logging.debug('Turnstile challenge not found')
        return None
    logging.info("Turnstile challenge detected. Selector found: " + _TURNSTILE_SELECTOR)
    return _get_turnstile_token(driver=driver, tabs=tabs, deadline=deadline)


def _post_request(req: V1RequestBase, driver: WebDriver):
    html_content = build_post_html(req.url, req.postData)
    driver.get("data:text/html;charset=utf-8," + html_content)
