"""Browser-free tests for which engine a request goes to, and what is learned.

Per-host memory is what stops a host only one engine can clear from paying for
the other engine's failure on every request. It decides the order, so what gets
written into it, and when it is consulted, both matter.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_engine_routing
"""
import unittest
from unittest.mock import patch

import flaresolverr_service
from dtos import V1RequestBase
from engines.base import SolveResult

SOLVED = "<html><title>Real page</title>ok</html>"
CHALLENGE = "<html><title>Just a moment...</title>window._cf_chl_opt</html>"


class FakeEngine:
    def __init__(self, name, response=SOLVED):
        self.name = name
        self.response = response
        self.called = False

    def solve(self, req, method, timeout):
        self.called = True
        result = SolveResult()
        result.url = "https://example-site.tld/"
        result.response = self.response
        result.message = "Challenge solved!"
        return result


def request(**fields):
    return V1RequestBase(dict({"cmd": "request.get", "url": "https://example-site.tld/",
                               "maxTimeout": 60000}, **fields))


class PerHostMemory(unittest.TestCase):

    def setUp(self):
        flaresolverr_service._DOMAIN_ENGINE.clear()

    def resolve(self, engines):
        with patch.object(flaresolverr_service, "_engine_plan", lambda r: engines):
            return flaresolverr_service._resolve_challenge(request(), "GET")

    def test_a_solved_page_teaches_the_host_its_engine(self):
        self.resolve([FakeEngine("stealth")])

        self.assertEqual(flaresolverr_service._recalled_engine("example-site.tld"), "stealth")

    def test_a_page_that_still_looks_challenged_teaches_nothing(self):
        # The last engine returns its page rather than an error, but it did not
        # clear anything, so sending later requests there first is a downgrade.
        self.resolve([FakeEngine("chrome", response=CHALLENGE)])

        self.assertIsNone(flaresolverr_service._recalled_engine("example-site.tld"))


class SessionInBothPools(unittest.TestCase):
    """A fallback leaves one id live in both pools; memory picks between them."""

    def setUp(self):
        flaresolverr_service._DOMAIN_ENGINE.clear()

    def plan_for(self, recalled):
        chrome, stealth = FakeEngine("chrome"), FakeEngine("stealth")
        with patch.object(flaresolverr_service, "_available_engines",
                          lambda: {"chrome": chrome, "stealth": stealth}), \
                patch.object(flaresolverr_service, "_pool_has", lambda name, sid: True), \
                patch.object(flaresolverr_service, "_recalled_engine", lambda host: recalled):
            order = flaresolverr_service._engine_plan(request(session="s"))
        return [engine.name for engine in order]

    def test_the_remembered_engine_goes_first(self):
        self.assertEqual(self.plan_for("stealth")[0], "stealth")

    def test_nothing_remembered_still_gives_a_plan(self):
        self.assertEqual(self.plan_for(None)[0], "chrome")


class FakeStealth:
    name = "stealth"

    def __init__(self):
        self.created = []

    def create_session(self, session_id=None, proxy=None):
        self.created.append(session_id)
        return session_id or "generated", True

    def exists(self, session_id):
        return False


class SessionsCreate(unittest.TestCase):

    def create(self, stealth, **fields):
        req = V1RequestBase(dict({"cmd": "sessions.create"}, **fields))
        with patch.object(flaresolverr_service, "STEALTH_ENGINE", stealth),                 patch.object(flaresolverr_service.config, "default_engine", lambda: "stealth"):
            return flaresolverr_service._cmd_sessions_create(req)

    def test_auto_takes_the_default_engine_rather_than_chrome(self):
        stealth = FakeStealth()

        self.create(stealth, engine="auto", session="s")

        self.assertEqual(stealth.created, ["s"])

    def test_an_id_already_live_in_the_other_pool_is_not_built_again(self):
        stealth = FakeStealth()

        with patch.object(flaresolverr_service, "_pool_has", lambda name, sid: name == "chrome"):
            res = self.create(stealth, engine="stealth", session="s")

        self.assertEqual(res.message, "Session already exists.")


if __name__ == "__main__":
    unittest.main()
