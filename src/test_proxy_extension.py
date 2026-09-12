"""Browser-free test for the proxy extension's lifetime.

The extension directory holds the proxy username and password in plaintext, and
a browser launch that raised used to skip the cleanup and leave them in the
system temp directory. Proxy *shape* is covered by test_geo.ProxyShapeTest,
where proxy_to_config lives.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_proxy_extension
"""
import os
import unittest
from unittest.mock import patch

import geo
import utils


class ProxyExtensionCleanupTest(unittest.TestCase):
    """The extension directory holds the proxy password, so it never outlives the launch."""

    PROXY = {"url": "http://p:1", "username": "u", "password": "secret"}

    def _launch(self, chrome, chrome_exe=lambda: "/bin/chromium"):
        """Drive get_webdriver with the machine-dependent boundaries stubbed out."""
        created = []
        real_create = utils.create_proxy_extension

        def _create(proxy):
            path = real_create(proxy)
            created.append(path)
            return path

        with patch.object(utils, 'create_proxy_extension', side_effect=_create), \
                patch.object(utils, 'get_chrome_exe_path', side_effect=chrome_exe), \
                patch.object(utils, 'get_chrome_major_version', return_value="151"), \
                patch.object(utils, 'start_xvfb_display'), \
                patch.object(geo, 'browser_language', return_value="en-US"), \
                patch.object(geo, 'browser_timezone', return_value="UTC"), \
                patch('undetected_chromedriver.Chrome', side_effect=chrome):
            try:
                utils.get_webdriver(self.PROXY)
            except Exception:
                pass
        self.assertEqual(len(created), 1, "the test needs exactly one extension directory")
        return created[0]

    def test_a_failed_launch_leaves_no_credentials_behind(self):
        path = self._launch(chrome=RuntimeError("session not created"))
        self.assertFalse(os.path.exists(path))

    def test_a_successful_launch_leaves_no_credentials_behind(self):
        path = self._launch(chrome=lambda **kwargs: _FakeDriver())
        self.assertFalse(os.path.exists(path))

    def test_a_failure_before_the_launch_leaves_no_credentials_behind(self):
        # Finding Chrome, reading its version and starting the display all run
        # after the extension is written, and all three can raise.
        path = self._launch(chrome=lambda **kwargs: _FakeDriver(),
                            chrome_exe=RuntimeError("no Chrome on this host"))
        self.assertFalse(os.path.exists(path))


class _FakeDriver:
    """Enough of the driver for get_webdriver to finish its post-launch work."""

    class _Patcher:
        data_path = None
        exe_name = "chromedriver"
        executable_path = "/app/chromedriver"

    patcher = _Patcher()

    def __getattr__(self, _name):
        return lambda *a, **k: None


if __name__ == '__main__':
    unittest.main()


class BrowserLanguageFlagsTest(unittest.TestCase):
    """What the launch tells Chrome about language, and in which flag.

    undetected_chromedriver reads Chrome's --lang off the last argument whose
    name contains "lang", so the Accept-Language header pair reached it as a UI
    language of "de-DE, de" until the tag was passed in its own flag.
    """

    def flags(self):
        seen = {}

        def chrome(options=None, **_kwargs):
            seen['args'] = list(options.arguments)
            raise Exception("not launching a browser in a test")

        with patch.object(utils, 'get_chrome_exe_path', return_value="/bin/chromium"), \
                patch.object(utils, 'get_chrome_major_version', return_value="151"), \
                patch.object(utils, 'start_xvfb_display'), \
                patch.object(geo, 'browser_language', return_value="de-DE"), \
                patch.object(geo, 'browser_timezone', return_value="UTC"), \
                patch('undetected_chromedriver.Chrome', side_effect=chrome):
            try:
                utils.get_webdriver()
            except Exception:
                pass
        return seen['args']

    def test_the_header_carries_the_tag_and_its_base(self):
        self.assertIn('--accept-lang=de-DE, de', self.flags())

    def test_the_ui_language_is_one_tag(self):
        # The last "lang" argument is the one the driver reads.
        langs = [a for a in self.flags() if 'lang' in a]
        self.assertEqual(langs[-1], '--lang=de-DE')


class ExtensionCreationFailureTest(unittest.TestCase):
    """The directory holds the credentials from its second write onward."""

    def test_a_failed_write_leaves_no_directory_behind(self):
        created = []
        real_mkdtemp = utils.tempfile.mkdtemp

        def mkdtemp(*a, **k):
            path = real_mkdtemp(*a, **k)
            created.append(path)
            return path

        real_open = open

        def failing_open(path, *a, **k):
            if str(path).endswith("background.js"):
                raise OSError("disk full")
            return real_open(path, *a, **k)

        with patch.object(utils.tempfile, "mkdtemp", mkdtemp), \
                patch("builtins.open", failing_open):
            with self.assertRaises(OSError):
                utils.create_proxy_extension({"url": "http://p:1", "username": "u",
                                              "password": "secret"})

        self.assertFalse(os.path.exists(created[0]))
