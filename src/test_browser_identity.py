"""Browser-free tests for what the browser says it is.

Two things a solve reports about its browser, both easy to get subtly wrong:
the language it runs in (LANG, shared by both engines, and worse than useless
if it reaches the browser un-normalized) and the user agent it sent.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_browser_identity
"""
import os
import unittest
from unittest.mock import patch

import config
from engines.stealth_engine import _user_agent_from


def _locale_for(value):
    """config.browser_locale() with LANG set to `value` (None means unset)."""
    env = {k: v for k, v in os.environ.items() if k != 'LANG'}
    if value is not None:
        env['LANG'] = value
    with patch.dict(os.environ, env, clear=True):
        return config.browser_locale()


class _Response:
    """Stands in for a Playwright Response's request headers."""

    def __init__(self, headers):
        self.request = type('Request', (), {'headers': headers})()


class BrowserLocaleTest(unittest.TestCase):

    def test_unset_lang_leaves_the_language_to_the_engine(self):
        self.assertIsNone(_locale_for(None))

    def test_empty_lang_leaves_the_language_to_the_engine(self):
        self.assertIsNone(_locale_for('   '))

    def test_posix_codeset_is_stripped(self):
        self.assertEqual(_locale_for('en_US.UTF-8'), 'en-US')

    def test_posix_modifier_is_stripped(self):
        self.assertEqual(_locale_for('de_DE@euro'), 'de-DE')

    def test_language_tag_survives_unchanged(self):
        self.assertEqual(_locale_for('pt-BR'), 'pt-BR')

    def test_bare_language_is_accepted(self):
        self.assertEqual(_locale_for('fr'), 'fr')

    def test_region_is_upper_cased(self):
        self.assertEqual(_locale_for('en_us'), 'en-US')

    def test_script_subtag_is_kept(self):
        self.assertEqual(_locale_for('zh_Hans_CN'), 'zh-Hans-CN')

    def test_numeric_region_is_accepted(self):
        self.assertEqual(_locale_for('es-419'), 'es-419')

    def test_c_locale_is_ignored(self):
        self.assertIsNone(_locale_for('C.UTF-8'))

    def test_posix_locale_is_ignored(self):
        self.assertIsNone(_locale_for('POSIX'))

    def test_unparseable_lang_is_ignored(self):
        with self.assertLogs(level='WARNING'):
            self.assertIsNone(_locale_for('not a language'))

    def test_unparseable_lang_says_so(self):
        config._rejected_langs.discard('gibberish!')
        with self.assertLogs(level='WARNING') as logs:
            _locale_for('gibberish!')
        self.assertIn('gibberish!', logs.output[0])


class StealthUserAgentTest(unittest.TestCase):

    def test_user_agent_comes_from_the_navigation_request(self):
        self.assertEqual(_user_agent_from(_Response({'user-agent': 'Mozilla/5.0 (X11)'})),
                         'Mozilla/5.0 (X11)')

    def test_no_navigation_response_reports_no_user_agent(self):
        self.assertEqual(_user_agent_from(None), '')

    def test_request_without_the_header_reports_no_user_agent(self):
        self.assertEqual(_user_agent_from(_Response({'accept': '*/*'})), '')

    def test_unreadable_request_reports_no_user_agent(self):
        self.assertEqual(_user_agent_from(object()), '')


if __name__ == '__main__':
    unittest.main()
