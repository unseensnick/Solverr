"""Challenge and access-denied detection constants.

Single source of truth shared by every engine (Chrome/Selenium and
Camoufox/Playwright) so detection coverage is identical no matter which engine
solves a request. Ported from FlareSolverr's original in-line lists.
"""
import re

ACCESS_DENIED_TITLES = [
    # Cloudflare
    'Access denied',
    # Cloudflare http://bitturk.net/ Firefox
    'Attention Required! | Cloudflare'
]
ACCESS_DENIED_SELECTORS = [
    # Cloudflare
    'div.cf-error-title span.cf-code-label span',
    # Cloudflare http://bitturk.net/ Firefox
    '#cf-error-details div.cf-error-overview h1'
]
CHALLENGE_TITLES = [
    # Cloudflare
    'Just a moment...',
    # DDoS-GUARD
    'DDoS-Guard'
]
CHALLENGE_SELECTORS = [
    # Cloudflare
    '#cf-challenge-running', '.ray_id', '.attack-box', '#cf-please-wait', '#challenge-spinner', '#trk_jschal_js', '#turnstile-wrapper', '.lds-ring',
    # Language-independent Cloudflare interstitial markers. The challenge title is
    # localized (e.g. "Vent litt ..." in Norwegian), so title matching alone misses
    # it when the browser locale isn't English; these are present regardless.
    'script[src*="/cdn-cgi/challenge-platform/"]', '#challenge-form', '#challenge-stage',
    # Custom CloudFlare for EbookParadijs, Film-Paleis, MuziekFabriek and Puur-Hollands
    'td.info #js_info',
    # Fairlane / pararius.com
    'div.vc div.text-box h2'
]

TURNSTILE_SELECTORS = [
    "input[name='cf-turnstile-response']"
]

# Markers that identify an UNSOLVED Cloudflare challenge in returned HTML. Used
# post-solve to tell whether an engine handed back a challenge page instead of
# real content, so it must be precise: matching a marker forces a fallback or
# blocks caching. Cloudflare leaves a "/cdn-cgi/challenge-platform/" beacon on
# solved pages too, so that string is deliberately NOT here (it stays in
# CHALLENGE_SELECTORS for pre-solve browser detection, where over-matching only
# costs a redundant solve attempt on an already-clear page). The challenge-form /
# challenge-stage markers keep coverage for interstitials whose title is
# localized (e.g. "Vent litt ...") and so slips past the title check;
# turnstile-wrapper keeps coverage for an unsolved Turnstile gate.
CHALLENGE_HTML_MARKERS = (
    'window._cf_chl_opt',
    'cf-challenge-running',
    'id="challenge-form"',
    'id="challenge-stage"',
    'id="challenge-error',
    'turnstile-wrapper',
)


def looks_like_challenge_html(html: str) -> bool:
    """Whether returned HTML is itself an unsolved Cloudflare challenge page.

    Distinguishes a real interstitial (challenge title or an interstitial marker)
    from solved content that merely still carries the post-clearance beacon.
    """
    if not html:
        return False
    low = html.lower()
    match = re.search(r'<title[^>]*>(.*?)</title>', low, re.S)
    if match:
        title = match.group(1).strip()
        if any(t.lower() in title for t in CHALLENGE_TITLES):
            return True
    return any(marker in low for marker in CHALLENGE_HTML_MARKERS)
