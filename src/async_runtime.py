"""A single background asyncio event loop for the Playwright/Camoufox engine.

FlareSolverr's web server is synchronous (bottle + waitress, threaded). Playwright's
async API and, crucially, any *persistent* Camoufox context used by a session must
live on one long-running event loop: a browser context created inside one
``asyncio.run()`` cannot be reused by a later call. This module runs that loop on a
daemon thread; synchronous request threads submit coroutines to it via
``run_coroutine_threadsafe`` and block for the result.
"""
import asyncio
import logging
import threading
from concurrent.futures import TimeoutError as FuturesTimeout


class AsyncRuntime:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="stealth-loop", daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def run(self, coro, timeout=None):
        """Run ``coro`` on the background loop and block until it finishes.

        On timeout the underlying task is cancelled and ``FuturesTimeout`` is
        raised to the caller.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except FuturesTimeout:
            future.cancel()
            raise


_runtime = None
_lock = threading.Lock()


def get_runtime() -> AsyncRuntime:
    """Lazily create the process-wide runtime (starts the loop thread on first use)."""
    global _runtime
    if _runtime is None:
        with _lock:
            if _runtime is None:
                logging.debug("Starting stealth async runtime")
                _runtime = AsyncRuntime()
    return _runtime
