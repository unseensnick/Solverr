"""Browser-free tests for the BROWSER_TIMEZONE both engines are pinned to.

An unchecked zone splits the engines rather than failing loudly: Chrome's CDP
override is refused and `_apply_timezone` swallows that, leaving the browser on
the container's zone, while the stealth side hands the same string to Camoufox.
So a value the system's timezone table does not list is dropped here, before
either browser sees it, the way LANG and BROWSER_GEO already are.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_timezone_validation
"""
import os
import unittest
from unittest.mock import patch

import config
import geo

ZONES = {"Europe/Stockholm", "America/Chicago"}


def _env(**overrides):
    """os.environ with the timezone and language settings set only as given."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('BROWSER_TIMEZONE', 'BROWSER_GEO', 'LANG')}
    env.update({k: v for k, v in overrides.items() if v is not None})
    return patch.dict(os.environ, env, clear=True)


def _with_zones(zones=ZONES):
    """Check against a fixed table rather than the host's tzdata, which the
    machine running the tests may not have at all."""
    return patch.object(geo, '_known_zones', lambda: zones)


class PinnedZone(unittest.TestCase):

    def setUp(self):
        config._rejected_zones.clear()

    def test_a_known_zone_is_kept(self):
        with _env(BROWSER_TIMEZONE='Europe/Stockholm'), _with_zones():
            self.assertEqual(config.browser_timezone(), 'Europe/Stockholm')

    def test_a_typo_is_dropped(self):
        with _env(BROWSER_TIMEZONE='Europe/Stockholmm'), _with_zones(), \
             self.assertLogs(level='WARNING'):
            self.assertIsNone(config.browser_timezone())

    def test_the_warning_names_the_value(self):
        with _env(BROWSER_TIMEZONE='Europe/Stockholmm'), _with_zones(), \
             self.assertLogs(level='WARNING') as logged:
            config.browser_timezone()

        self.assertIn('Europe/Stockholmm', logged.output[0])

    def test_a_repeated_bad_value_warns_only_once(self):
        # Read on every browser launch, so warning each time would bury the rest
        # of the request log.
        with _env(BROWSER_TIMEZONE='Europe/Stockholmm'), _with_zones():
            with self.assertLogs(level='WARNING'):
                config.browser_timezone()
            with self.assertNoLogs(level='WARNING'):
                config.browser_timezone()

    def test_auto_is_not_checked_against_the_table(self):
        with _env(BROWSER_TIMEZONE='auto'), _with_zones():
            self.assertEqual(config.browser_timezone(), 'auto')

    def test_a_host_with_no_timezone_table_keeps_the_value(self):
        # Nothing to check against, and geo warns about the missing table
        # itself, so the value goes through unverified rather than dropped.
        with _env(BROWSER_TIMEZONE='Europe/Stockholmm'), _with_zones(set()):
            self.assertEqual(config.browser_timezone(), 'Europe/Stockholmm')

    def test_a_dropped_zone_leaves_the_engines_following_the_exit_ip(self):
        with _env(BROWSER_TIMEZONE='Europe/Stockholmm'), _with_zones(), \
             self.assertLogs(level='WARNING'):
            self.assertIsNone(geo._pinned_zone())


if __name__ == '__main__':
    unittest.main()
