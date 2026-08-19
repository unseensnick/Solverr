"""Optional passthrough proxy (dormant unless PASSTHROUGH_ENABLED=true).

Serves solved page bodies over plain HTTP on a second port. A client that would
otherwise re-fetch the URL itself, and trip Cloudflare's fingerprinting on that
replay, instead points at this port and consumes the solved HTML directly, so it
never sees a challenge. The upstream host is taken from the first path segment
and must be listed in PASSTHROUGH_ALLOWED_HOSTS, so this is never a blind open
proxy. A path whose first segment is not an allow-listed host is treated as a
site-internal absolute link (e.g. /details/...) and routed to the default mirror
(the first allow-listed host), so a client following the site's own links still
comes back through the proxy. Requests are solved in-process through the same
controller as /v1, reusing engine selection, fallback, sessions, and per-host
memory.

The passthrough approach was demonstrated by the byparr-proxy project
(https://github.com/guyg2232/byparr-proxy); this is an independent
reimplementation wired directly into the controller.
"""
import base64
import logging
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import detection
import flaresolverr_service
from dtos import STATUS_OK, V1RequestBase

# Static assets a client never needs from us; forwarding each would waste a full
# solve cycle. Answered with 404 without touching the solver.
_SKIP_EXT = re.compile(
    r"\.(css|js|mjs|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|mp4|webm)(\?|$)",
    re.IGNORECASE,
)

# Populated once by start() from config, so each request avoids re-reading env.
_ALLOWED_HOSTS = set()
_DEFAULT_HOST = None
_CACHE_TTL = 0
_CACHE_MAX_BYTES = 0
_TIMEOUT_MS = 120000

# One body may occupy at most this share of the cap. Without it a single large
# document evicts most of the cache to make room for itself, which is worse than
# not caching it at all.
_MAX_BODY_SHARE = 0.25

_HTML_CONTENT_TYPE = "text/html; charset=utf-8"

_cache = {}       # request path -> (expires_monotonic, status, body_bytes, content_type)
_cache_bytes = 0  # running total of the body bytes in _cache, guarded by _lock
_inflight = {}    # request path -> _Pending
_lock = threading.Lock()


class _Pending:
    """Shared slot so concurrent requests for the same path wait on one solve."""
    __slots__ = ("event", "status", "body", "content_type", "error")

    def __init__(self):
        self.event = threading.Event()
        self.status = None
        self.body = None
        self.content_type = _HTML_CONTENT_TYPE
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
    content_type, solution). Raises on solver failure."""
    req = V1RequestBase({"cmd": "request.get", "url": target, "maxTimeout": _TIMEOUT_MS})
    _apply_env_proxy(req)
    res = flaresolverr_service.controller_v1_endpoint(req)
    if getattr(res, '__error_500__', False) or res.status != STATUS_OK or res.solution is None:
        raise RuntimeError(res.message or "solver returned an error")
    status = res.solution.status or 200
    raw = res.solution.response or ""
    # A non-HTML document (currently only PDF) comes back base64-encoded, so
    # decode it and serve the real bytes under their own content type.
    if getattr(res.solution, 'contentType', None) == "application/pdf":
        return status, base64.b64decode(raw), "application/pdf", res.solution
    return status, raw.encode("utf-8", errors="replace"), _HTML_CONTENT_TYPE, res.solution


def reset_cache() -> None:
    """Drop every cached body. Exists for tests; the server never needs it."""
    global _cache_bytes
    with _lock:
        _cache.clear()
        _cache_bytes = 0


def _cache_store(raw: str, status: int, body: bytes, content_type: str) -> bool:
    """Cache `body` under `raw`, evicting as needed to stay under the byte cap.

    Returns whether it was stored, which is not the same as whether it was
    eligible: a body over the per-body ceiling is refused outright.

    Age alone used to bound this. Expired entries were pruned so nothing
    outlived its TTL, but nothing capped how much could accumulate inside one
    TTL window, so a client walking pagination for an hour pinned every distinct
    body for that hour. Non-HTML documents are held as decoded bytes, which is
    what makes the total worth measuring in bytes rather than entries.
    """
    global _cache_bytes
    size = len(body)
    if 0 < _CACHE_MAX_BYTES < size / _MAX_BODY_SHARE:
        logging.debug("[pt] %s not cached: %d bytes is over the per-body ceiling", raw, size)
        return False

    with _lock:
        stored_at = time.monotonic()
        # Reading an entry only skips it once it expires, so drop the dead ones
        # here: every distinct path a client crawls would otherwise pin its body
        # for the process lifetime.
        for stale in [k for k, v in _cache.items() if v[0] <= stored_at]:
            _drop(stale)
        # Re-storing a path replaces it, so its old bytes leave the total first.
        _drop(raw)
        if _CACHE_MAX_BYTES > 0:
            # Soonest-expiring first, so eviction takes what was going to go anyway.
            for key in sorted(_cache, key=lambda k: _cache[k][0]):
                if _cache_bytes + size <= _CACHE_MAX_BYTES:
                    break
                _drop(key)
        _cache[raw] = (stored_at + _CACHE_TTL, status, body, content_type)
        _cache_bytes += size
    return True


def _drop(key: str) -> None:
    """Remove one entry and its bytes from the total. Caller holds ``_lock``."""
    global _cache_bytes
    entry = _cache.pop(key, None)
    if entry is not None:
        _cache_bytes -= len(entry[2])


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes = b"", content_type: str = _HTML_CONTENT_TYPE):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionError):
            # Client (e.g. Prowlarr) gave up and closed the socket mid-write,
            # usually after its own request timeout. Nothing to send to.
            logging.debug("[pt] client disconnected before the response completed")

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
            if "." in host:
                # Looks like a hostname but isn't allow-listed: a mirror the
                # deployer forgot to add to PASSTHROUGH_ALLOWED_HOSTS.
                logging.warning("[pt %s] %s %s -> 403 (host '%s' not in PASSTHROUGH_ALLOWED_HOSTS)",
                                rid, self.command, raw, host)
                self._send(403, b"host not allowed")
                return
            # A site-internal absolute link (e.g. /details/...) that resolved
            # against the origin and lost its mirror segment. Route it to the
            # default mirror with the path intact so downloads and pagination work.
            if _DEFAULT_HOST is None:
                self._send(404)
                return
            host = _DEFAULT_HOST
            remainder = raw if raw.startswith("/") else "/" + raw

        target = "https://" + host + remainder
        now = time.monotonic()

        with _lock:
            entry = _cache.get(raw)
            if entry and _CACHE_TTL > 0 and entry[0] > now:
                logging.info("[pt %s] %s %s <- cache hit", rid, self.command, raw)
                self._send(entry[1], entry[2], entry[3])
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
            self._send(pending.status, pending.body, pending.content_type)
            return

        logging.info("[pt %s] %s %s from %s -> solving %s",
                     rid, self.command, raw, self.address_string(), target)
        started = time.monotonic()
        # Whatever happens below, this slot has to be released and the waiters
        # woken. Leaving either undone strands every later request for this path:
        # they would each wait the full timeout and then 502, for the life of the
        # process, because the slot says a solve is still running.
        try:
            try:
                status, body, content_type, solution = _solve(target)
            except Exception as e:
                pending.error = e
                logging.error("[pt %s] %s %s <- 502 after %.1fs: %s",
                              rid, self.command, raw, time.monotonic() - started, e)
                self._send(502, b"solver error")
                return

            # Don't pin a challenge page or a non-2xx for the whole TTL: a
            # transient block would otherwise be served from cache long after it
            # cleared. Both engines currently hardcode solution.status to 200, so
            # today it is the challenge check that does the filtering; the status
            # check is here for when an engine can report the real one.
            cacheable = (
                _CACHE_TTL > 0 and 200 <= status < 300
                and not detection.looks_like_challenge_html(solution.response)
            )
            # Eligible is not the same as stored: the byte cap can still refuse it,
            # so the log below reports what actually happened.
            cached = cacheable and _cache_store(raw, status, body, content_type)
            pending.status = status
            pending.body = body
            pending.content_type = content_type
            logging.info("[pt %s] %s %s <- %d in %.1fs (%d bytes%s)",
                         rid, self.command, raw, status, time.monotonic() - started,
                         len(body), ", cached" if cached else "")
            self._send(status, body, content_type)
        finally:
            with _lock:
                _inflight.pop(raw, None)
            pending.event.set()

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

    global _ALLOWED_HOSTS, _DEFAULT_HOST, _CACHE_TTL, _CACHE_MAX_BYTES, _TIMEOUT_MS
    hosts = config.passthrough_allowed_hosts()
    _ALLOWED_HOSTS = set(hosts)
    # First allow-listed host is the mirror used for site-internal absolute links.
    _DEFAULT_HOST = hosts[0] if hosts else None
    _CACHE_TTL = config.passthrough_cache_ttl()
    _CACHE_MAX_BYTES = config.passthrough_cache_max_bytes()
    _TIMEOUT_MS = config.passthrough_timeout_ms()
    port = config.passthrough_port()

    logging.info("Passthrough proxy enabled on port %d", port)
    if hosts:
        logging.info("  allowed hosts: %s (default: %s)", ", ".join(hosts), _DEFAULT_HOST)
    else:
        logging.warning("  PASSTHROUGH_ALLOWED_HOSTS is empty; every request is refused (403)")
    if _CACHE_MAX_BYTES <= 0:
        cap = "unbounded"
    elif _CACHE_MAX_BYTES >= 1024 * 1024:
        cap = f"{_CACHE_MAX_BYTES // (1024 * 1024)} MB"
    else:
        # Reporting a sub-megabyte cap in whole MB rounds it to "0 MB", which
        # reads as caching being off rather than tight.
        cap = f"{_CACHE_MAX_BYTES} bytes"
    logging.info("  cache ttl: %ds (max %s), request timeout: %dms", _CACHE_TTL, cap, _TIMEOUT_MS)

    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="passthrough").start()
