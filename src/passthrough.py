"""Optional passthrough proxy (dormant unless PASSTHROUGH_ENABLED=true).

Serves solved page bodies over plain HTTP on a second port. A client that would
otherwise re-fetch the URL itself, and trip Cloudflare's fingerprinting on that
replay, instead points at this port and consumes the solved HTML directly, so it
never sees a challenge. The upstream host is taken from the first path segment
and must be listed in PASSTHROUGH_ALLOWED_HOSTS, so this is never a blind open
proxy. Requests are solved in-process through the same controller as /v1,
reusing engine selection, fallback, sessions, and per-host memory.

The passthrough approach was demonstrated by the byparr-proxy project
(https://github.com/guyg2232/byparr-proxy); this is an independent
reimplementation wired directly into the controller.
"""
import logging
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import flaresolverr_service
from detection import CHALLENGE_TITLES
from dtos import STATUS_OK, V1RequestBase

# Static assets a client never needs from us; forwarding each would waste a full
# solve cycle. Answered with 404 without touching the solver.
_SKIP_EXT = re.compile(
    r"\.(css|js|mjs|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|mp4|webm)(\?|$)",
    re.IGNORECASE,
)

# Strong signals that a RETURNED page is itself an unsolved interstitial, used to
# keep such a page out of the cache. Deliberately narrower than the controller's
# fallback heuristic: Cloudflare's benign "/cdn-cgi/challenge-platform/" beacon
# rides along on solved pages too, so matching it here would stop every solved
# page from ever being cached.
_CHALLENGE_PAGE_MARKERS = ('id="challenge-form"', 'id="challenge-stage"', 'cf-challenge-running')

# Populated once by start() from config, so each request avoids re-reading env.
_ALLOWED_HOSTS = set()
_CACHE_TTL = 0
_TIMEOUT_MS = 120000

_cache = {}       # request path -> (expires_monotonic, status, body_bytes)
_inflight = {}    # request path -> _Pending
_lock = threading.Lock()


class _Pending:
    """Shared slot so concurrent requests for the same path wait on one solve."""
    __slots__ = ("event", "status", "body", "error")

    def __init__(self):
        self.event = threading.Event()
        self.status = None
        self.body = None
        self.error = None


def _split_host(raw_path: str):
    """Split '/<host>/<rest>?<query>' into (host, '/<rest>?<query>').

    Returns (None, None) when no usable host segment is present.
    """
    body = raw_path[1:] if raw_path.startswith("/") else raw_path
    if not body or body[0] in "?#":
        return None, None
    if "/" in body:
        host, rest = body.split("/", 1)
        remainder = "/" + rest
    else:
        host, sep, query = body.partition("?")
        remainder = "/" + ("?" + query if sep else "")
    host = host.strip().lower()
    if not host or "?" in host or "#" in host:
        return None, None
    return host, remainder


def _looks_unsolved(html: str) -> bool:
    """Whether the returned HTML is itself an unsolved challenge page (so it must
    not be cached). Matches the challenge title or challenge-form markers, not the
    post-clearance beacon that solved pages also carry."""
    if not html:
        return False
    low = html.lower()
    match = re.search(r'<title[^>]*>(.*?)</title>', low, re.S)
    if match:
        title = match.group(1).strip()
        if any(t.lower() in title for t in CHALLENGE_TITLES):
            return True
    return any(marker in low for marker in _CHALLENGE_PAGE_MARKERS)


def _apply_env_proxy(req: V1RequestBase) -> None:
    """Mirror the PROXY_URL injection the /v1 route does, so passthrough solves
    use the same configured (e.g. residential) proxy. Engines read req.proxy."""
    url = os.environ.get('PROXY_URL')
    if not url:
        return
    username = os.environ.get('PROXY_USERNAME')
    password = os.environ.get('PROXY_PASSWORD')
    if username is None and password is None:
        req.proxy = {"url": url}
    else:
        req.proxy = {"url": url, "username": username, "password": password}


def _solve(target: str):
    """Solve `target` in-process via the controller. Returns (status, body_bytes,
    solution). Raises on solver failure."""
    req = V1RequestBase({"cmd": "request.get", "url": target, "maxTimeout": _TIMEOUT_MS})
    _apply_env_proxy(req)
    res = flaresolverr_service.controller_v1_endpoint(req)
    if getattr(res, '__error_500__', False) or res.status != STATUS_OK or res.solution is None:
        raise RuntimeError(res.message or "solver returned an error")
    status = res.solution.status or 200
    body = (res.solution.response or "").encode("utf-8", errors="replace")
    return status, body, res.solution


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes = b""):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _handle(self):
        rid = uuid.uuid4().hex[:6]
        raw = self.path

        if _SKIP_EXT.search(raw):
            logging.debug("[pt %s] %s %s -> 404 (static asset)", rid, self.command, raw)
            self._send(404)
            return

        host, remainder = _split_host(raw)
        if host is None:
            self._send(404)
            return
        if host not in _ALLOWED_HOSTS:
            logging.warning("[pt %s] %s %s -> 403 (host '%s' not in PASSTHROUGH_ALLOWED_HOSTS)",
                            rid, self.command, raw, host)
            self._send(403, b"host not allowed")
            return

        target = "https://" + host + remainder
        now = time.monotonic()

        with _lock:
            entry = _cache.get(raw)
            if entry and _CACHE_TTL > 0 and entry[0] > now:
                logging.info("[pt %s] %s %s <- cache hit", rid, self.command, raw)
                self._send(entry[1], entry[2])
                return
            pending = _inflight.get(raw)
            owner = pending is None
            if owner:
                pending = _Pending()
                _inflight[raw] = pending

        if not owner:
            pending.event.wait(timeout=_TIMEOUT_MS / 1000 + 30)
            if pending.error is not None or pending.status is None:
                self._send(502, b"solver error")
                return
            logging.info("[pt %s] %s %s <- coalesced (%d bytes)",
                         rid, self.command, raw, len(pending.body))
            self._send(pending.status, pending.body)
            return

        logging.info("[pt %s] %s %s from %s -> solving %s",
                     rid, self.command, raw, self.address_string(), target)
        started = time.monotonic()
        try:
            status, body, solution = _solve(target)
        except Exception as e:
            with _lock:
                _inflight.pop(raw, None)
            pending.error = e
            pending.event.set()
            logging.error("[pt %s] %s %s <- 502 after %.1fs: %s",
                          rid, self.command, raw, time.monotonic() - started, e)
            self._send(502, b"solver error")
            return

        # Don't pin a challenge page or a non-2xx for the whole TTL: a transient
        # block would otherwise be served from cache long after it cleared.
        cacheable = (
            _CACHE_TTL > 0 and 200 <= status < 300
            and not _looks_unsolved(solution.response)
        )
        with _lock:
            _inflight.pop(raw, None)
            if cacheable:
                _cache[raw] = (time.monotonic() + _CACHE_TTL, status, body)
        pending.status = status
        pending.body = body
        pending.event.set()
        logging.info("[pt %s] %s %s <- %d in %.1fs (%d bytes%s)",
                     rid, self.command, raw, status, time.monotonic() - started,
                     len(body), ", cached" if cacheable else "")
        self._send(status, body)

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def log_message(self, fmt, *args):
        # Structured lines are emitted from _handle(); silence the default logging.
        pass


def start():
    """Launch the passthrough server in a daemon thread if enabled. No-op otherwise."""
    if not config.passthrough_enabled():
        return

    global _ALLOWED_HOSTS, _CACHE_TTL, _TIMEOUT_MS
    _ALLOWED_HOSTS = set(config.passthrough_allowed_hosts())
    _CACHE_TTL = config.passthrough_cache_ttl()
    _TIMEOUT_MS = config.passthrough_timeout_ms()
    port = config.passthrough_port()

    logging.info("Passthrough proxy enabled on port %d", port)
    if _ALLOWED_HOSTS:
        logging.info("  allowed hosts: %s", ", ".join(sorted(_ALLOWED_HOSTS)))
    else:
        logging.warning("  PASSTHROUGH_ALLOWED_HOSTS is empty; every request is refused (403)")
    logging.info("  cache ttl: %ds, request timeout: %dms", _CACHE_TTL, _TIMEOUT_MS)

    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="passthrough").start()
