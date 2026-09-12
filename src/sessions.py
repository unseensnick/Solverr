import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from uuid import uuid1

from typing import Any, Callable


@dataclass
class Session:
    session_id: str
    # What the engine keeps alive for this session: a Selenium WebDriver on the
    # Chrome side, a Camoufox context on the stealth side. The store never looks
    # inside it; only the engine's own teardown does.
    payload: Any
    created_at: datetime
    last_used: datetime = field(default=None)  # type: ignore[assignment]
    # Requests currently solving on this driver. Guarded by SessionStore's
    # lock, and read by the reaper so it never quits a browser mid-request.
    in_use: int = 0
    # The proxy this session's browser actually exits through. Kept because the
    # /v1 contract puts the proxy on sessions.create and ignores it on every
    # later request, so the request that triggers a rebuild does not carry it.
    proxy: Optional[dict] = None
    # Held for the whole solve, so two requests naming one session take its
    # browser in turn. A browser has one page: driving it from two threads at
    # once navigates under the other request and can answer it with the wrong
    # page. The stealth engine had this on its context from the start; the
    # Chrome pool did not, and it is one rule, so it lives on the session.
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        if self.last_used is None:
            self.last_used = self.created_at

    def lifetime(self) -> timedelta:
        return datetime.now() - self.created_at

    def idle(self) -> timedelta:
        return datetime.now() - self.last_used


# The proxy each session id was created with, shared by both engines' stores.
# A session's egress belongs to the id, not to the request that happens to name
# it next: clients set the proxy on sessions.create and omit it afterwards, and
# the same id legitimately exists in both pools after an engine fallback. Kept
# when a session is reaped, capped or expires, so the rebuild goes back out
# through the same exit instead of the server's own address; dropped only when
# the client destroys the session.
_PROXY_BY_ID: "OrderedDict[str, Optional[dict]]" = OrderedDict()
_PROXY_BY_ID_LOCK = threading.Lock()
# Session ids come from the client, so this is bounded: a client that invents
# ids and never destroys them evicts its own oldest entries rather than growing
# the map without limit.
_PROXY_MEMORY = 1024


def _remember_proxy(session_id: str, proxy: Optional[dict]) -> None:
    with _PROXY_BY_ID_LOCK:
        _PROXY_BY_ID.pop(session_id, None)
        _PROXY_BY_ID[session_id] = proxy
        while len(_PROXY_BY_ID) > _PROXY_MEMORY:
            _PROXY_BY_ID.popitem(last=False)


def _recall_proxy(session_id: str) -> Optional[dict]:
    with _PROXY_BY_ID_LOCK:
        return _PROXY_BY_ID.get(session_id)


def _forget_proxy(session_id: str) -> None:
    with _PROXY_BY_ID_LOCK:
        _PROXY_BY_ID.pop(session_id, None)


class SessionStore:
    """Creates, stores and reaps sessions for one engine.

    Every lifecycle rule lives here and is shared: idempotent creation, the
    in-use mark that keeps the reaper off a live browser, TTL expiry, idle
    reaping and the per-engine cap. The engine supplies only ``build`` (make the
    thing a session holds, given a proxy) and ``teardown`` (close it). Both
    engines had this written out separately, with the same expiry race in each.

    Thread-safe: the session dict is guarded by a lock because request threads
    (create/get/destroy) and the background reaper touch it concurrently.
    Teardown always runs OUTSIDE the lock so a slow close never blocks other
    session operations.
    """

    def __init__(self, build: Callable, teardown: Callable):
        self.sessions = {}
        self._build = build
        self._teardown_payload = teardown
        self._lock = threading.Lock()

    def create(self, session_id: Optional[str] = None, proxy: Optional[dict] = None,
               force_new: Optional[bool] = False,
               claim: bool = False) -> Tuple[Session, bool]:
        """create creates new instance of WebDriver if necessary,
        assign defined (or newly generated) session_id to the instance
        and returns the session object. If a new session has been created
        second argument is set to True.

        Note: The function is idempotent, so in case if session_id
        already exists in the storage a new instance of WebDriver won't be created
        and existing session will be returned. Second argument defines if
        new session has been created (True) or an existing one was used (False).
        """
        session_id = session_id or str(uuid1())

        if force_new:
            self.destroy(session_id)

        with self._lock:
            existing = self.sessions.get(session_id)
            if existing is not None:
                # Claimed before the lock is released, never after. A session
                # handed back unclaimed is findable and idle for that instant,
                # which is long enough for the reaper or the cap to close its
                # browser before the caller can say it is using it.
                if claim:
                    self._claim(existing)
                return existing, False

        # Build outside the lock (launching a browser takes seconds).
        session = Session(session_id, self._build(proxy), datetime.now(), proxy=proxy)
        _remember_proxy(session_id, proxy)

        with self._lock:
            race = self.sessions.get(session_id)
            if race is None:
                self.sessions[session_id] = session
            if claim:
                self._claim(race if race is not None else session)
        if race is not None:
            # Another thread created the session while we were launching ours;
            # discard the extra browser and use theirs.
            self._teardown(session)
            return race, False

        return session, True

    def _claim(self, session: Session) -> None:
        """Mark a session in use. The caller must hold the lock."""
        session.last_used = datetime.now()
        session.in_use += 1

    def exists(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self.sessions

    def destroy(self, session_id: str) -> bool:
        """destroy closes the driver instance and removes session from the storage.
        The function is noop if session_id doesn't exist.
        The function returns True if session was found and destroyed,
        and False if session_id wasn't found.
        """
        with self._lock:
            session = self.sessions.pop(session_id, None)
        _forget_proxy(session_id)
        if session is None:
            return False
        self._teardown(session)
        return True

    def get(self, session_id: str, ttl: Optional[timedelta] = None,
            proxy: Optional[dict] = None) -> Tuple[Session, bool]:
        """Return the session **marked in use**, creating it if it isn't there.

        The caller owns the mark and must pass the session to ``end_use`` when it
        is done, which is what keeps the reaper and the cap off a live browser.
        Marking happens here rather than in the caller so nothing can evict the
        session between handing it out and the request starting on it.

        The proxy has to reach the create call below. Without it a session named
        by a request before it exists is born on a direct connection, which is
        silent: the browser still solves, just from the server's own address. A
        session that already exists keeps the proxy it was created with, which is
        what the rebuild below uses.
        """
        # The session's own proxy wins over whatever this request carries: /v1
        # ignores a request proxy when a session is named, so a request that
        # rebuilds a reaped or expired session must not redirect its exit.
        remembered = _recall_proxy(session_id)
        if remembered is not None:
            proxy = remembered

        # claim=True: the session comes back already marked, taken under the same
        # lock that found or stored it. Marking here instead left it findable and
        # idle for an instant, which the reaper or the cap can use to close its
        # browser before the caller ever says it is in use.
        session, fresh = self.create(session_id, proxy, claim=True)

        # A second acquisition is safe now, because the mark is held throughout:
        # nothing can evict this session, and in_use == 1 says this request is
        # its only holder, so replacing it cannot pull it out from under another.
        with self._lock:
            expired = (ttl is not None and not fresh
                       and session.lifetime() > ttl and session.in_use == 1)
            if expired:
                # Out of the pool while the lock is still held, so nothing can
                # find it between the decision and the browser closing.
                self.sessions.pop(session_id, None)

        if not expired:
            if ttl is not None and not fresh and session.lifetime() > ttl:
                logging.debug("session's lifetime has expired but a request is still on it, "
                              "so it is reused (session_id=%s)", session_id)
            return session, fresh

        logging.debug("session's lifetime has expired, so the session is recreated (session_id=%s)",
                      session_id)
        self._teardown(session)
        # The session's own proxy, not the caller's: this is a rebuild of an
        # existing session, and the request that triggered it carries no proxy.
        return self.create(session_id, session.proxy, claim=True)

    def touch(self, session_id: str) -> None:
        with self._lock:
            session = self.sessions.get(session_id)
        if session is not None:
            session.last_used = datetime.now()

    def end_use(self, session: Session) -> None:
        """Release the mark ``get`` took, so the session can be reaped again."""
        with self._lock:
            session.in_use = max(0, session.in_use - 1)

    def reap_idle(self, ttl: timedelta) -> List[str]:
        """Close and remove sessions idle longer than ``ttl``. Returns reaped ids."""
        if ttl is None or ttl.total_seconds() <= 0:
            return []
        now = datetime.now()
        with self._lock:
            # A session solving right now is not idle, whatever its timestamp
            # says: quitting the driver under it kills the request with an
            # "invalid session id" that the caller cannot do anything about.
            stale = [sid for sid, s in self.sessions.items()
                     if (now - s.last_used) > ttl and not s.in_use]
            popped = [self.sessions.pop(sid) for sid in stale]
        for session in popped:
            self._teardown(session)
        return [s.session_id for s in popped]

    def enforce_cap(self, max_sessions: int) -> List[str]:
        """Evict the oldest-idle sessions until at most ``max_sessions`` remain."""
        if max_sessions is None or max_sessions <= 0:
            return []
        with self._lock:
            if len(self.sessions) <= max_sessions:
                return []
            # Never evict a session mid-solve, for the reason above. The cap is
            # best-effort, so under full pressure this just evicts fewer.
            ordered = sorted((s for s in self.sessions.values() if not s.in_use),
                             key=lambda s: s.last_used)
            to_remove = ordered[: len(self.sessions) - max_sessions]
            for s in to_remove:
                self.sessions.pop(s.session_id, None)
        for session in to_remove:
            self._teardown(session)
        return [s.session_id for s in to_remove]

    def session_ids(self) -> List[str]:
        with self._lock:
            return list(self.sessions.keys())

    def _teardown(self, session: Session) -> None:
        try:
            self._teardown_payload(session.payload)
        except Exception:
            logging.debug("session teardown failed", exc_info=True)
