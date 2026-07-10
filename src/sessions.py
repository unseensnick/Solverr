import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from uuid import uuid1

from selenium.webdriver.chrome.webdriver import WebDriver

import utils


@dataclass
class Session:
    session_id: str
    driver: WebDriver
    created_at: datetime
    last_used: datetime = field(default=None)  # type: ignore[assignment]

    def __post_init__(self):
        if self.last_used is None:
            self.last_used = self.created_at

    def lifetime(self) -> timedelta:
        return datetime.now() - self.created_at

    def idle(self) -> timedelta:
        return datetime.now() - self.last_used


class SessionsStorage:
    """Creates, stores and reaps Chrome (Selenium) sessions.

    Thread-safe: the session dict is guarded by a lock because request threads
    (create/get/destroy) and the background reaper touch it concurrently. Browser
    teardown (driver.quit) always runs OUTSIDE the lock so a slow quit never
    blocks other session operations.
    """

    def __init__(self):
        self.sessions = {}
        self._lock = threading.Lock()

    def create(self, session_id: Optional[str] = None, proxy: Optional[dict] = None,
               force_new: Optional[bool] = False) -> Tuple[Session, bool]:
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
            return existing, False

        # Build the browser outside the lock (it can take several seconds).
        driver = utils.get_webdriver(proxy)
        session = Session(session_id, driver, datetime.now())

        with self._lock:
            race = self.sessions.get(session_id)
            if race is None:
                self.sessions[session_id] = session
        if race is not None:
            # Another thread created the session while we were launching ours;
            # discard the extra browser and use theirs.
            self._teardown(session)
            return race, False

        return session, True

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
        if session is None:
            return False
        self._teardown(session)
        return True

    def get(self, session_id: str, ttl: Optional[timedelta] = None) -> Tuple[Session, bool]:
        session, fresh = self.create(session_id)

        if ttl is not None and not fresh and session.lifetime() > ttl:
            logging.debug(f'session\'s lifetime has expired, so the session is recreated (session_id={session_id})')
            session, fresh = self.create(session_id, force_new=True)

        session.last_used = datetime.now()
        return session, fresh

    def touch(self, session_id: str) -> None:
        with self._lock:
            session = self.sessions.get(session_id)
        if session is not None:
            session.last_used = datetime.now()

    def reap_idle(self, ttl: timedelta) -> List[str]:
        """Close and remove sessions idle longer than ``ttl``. Returns reaped ids."""
        if ttl is None or ttl.total_seconds() <= 0:
            return []
        now = datetime.now()
        with self._lock:
            stale = [sid for sid, s in self.sessions.items() if (now - s.last_used) > ttl]
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
            ordered = sorted(self.sessions.values(), key=lambda s: s.last_used)
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
            if utils.PLATFORM_VERSION == "nt":
                session.driver.close()
            session.driver.quit()
        except Exception:
            logging.debug("Chrome session teardown failed", exc_info=True)
