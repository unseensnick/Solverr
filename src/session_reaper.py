"""Background reaper that closes idle and over-cap sessions.

Clients (including the reikai app) create a shared session and never call
sessions.destroy, and a mobile app can be killed before it ever could. Each
abandoned session keeps a real browser (Chrome or, heavier, Camoufox/Firefox)
running on the server. This daemon periodically closes sessions that have been
idle longer than the TTL and evicts any beyond the per-engine cap, so memory
can't leak no matter how clients behave.

Each manager (SessionsStorage, StealthEngine) implements ``reap_idle(ttl)`` and
``enforce_cap(max)`` returning the list of removed session ids.
"""
import logging
import threading
from datetime import timedelta


class SessionReaper:
    def __init__(self, managers, ttl: timedelta, max_sessions: int, interval_seconds: int):
        self._managers = managers
        self._ttl = ttl
        self._max = max_sessions
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="session-reaper", daemon=True)

    def start(self):
        if self._interval <= 0:
            logging.info("Session reaper disabled (REAPER_INTERVAL_SECONDS <= 0)")
            return
        logging.info("Session reaper started (%s, %s, every %ss)",
                     self._describe_ttl(), self._describe_cap(), self._interval)
        self._thread.start()

    def _describe_ttl(self) -> str:
        """A TTL of zero or less is `reap_idle` doing nothing, so say that.

        Printing `ttl=0` reads as the most aggressive setting there is, which is
        the opposite of what it means.
        """
        if self._ttl is None or self._ttl.total_seconds() <= 0:
            return "idle reaping off (SESSION_TTL_MINUTES <= 0)"
        return f"ttl={self._ttl}"

    def _describe_cap(self) -> str:
        """Same for the cap: zero or less is `enforce_cap` doing nothing."""
        if self._max is None or self._max <= 0:
            return "no session cap (SESSION_MAX <= 0)"
        return f"max={self._max}/engine"

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self._interval):
            for manager in self._managers:
                try:
                    reaped = manager.reap_idle(self._ttl)
                    if reaped:
                        logging.info("Reaped %d idle session(s): %s", len(reaped), reaped)
                    evicted = manager.enforce_cap(self._max)
                    if evicted:
                        logging.info("Evicted %d session(s) over cap: %s", len(evicted), evicted)
                except Exception:
                    logging.debug("session reaper iteration failed", exc_info=True)
