"""Browser-free test for the proxy extension's lifetime.

The extension directory holds the proxy username and password in plaintext, and
a browser launch that raised used to skip the cleanup and leave them in the
system temp directory. Proxy *shape* is covered by test_geo.ProxyShapeTest,
where proxy_to_config lives.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_proxy_extension
"""
import json
import os
import re
import shutil
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

    def test_the_header_carries_the_tag_and_its_base(self):
        self.assertIn('--accept-lang=de-DE, de', _launch_flags())

    def test_the_ui_language_is_one_tag(self):
        # The last "lang" argument is the one the driver reads.
        langs = [a for a in _launch_flags() if 'lang' in a]
        self.assertEqual(langs[-1], '--lang=de-DE')


class DiskAndFeatureFlagsTest(unittest.TestCase):
    """What the launch switches off in Chrome, and that none of it is lost."""

    def test_component_updates_are_off(self):
        self.assertIn('--disable-component-update', _launch_flags())

    def test_every_disabled_feature_travels_in_one_switch(self):
        # Chrome keeps only the last --disable-features it is given.
        switches = [a for a in _launch_flags() if a.startswith('--disable-features=')]
        self.assertEqual(len(switches), 1)

    def test_the_on_device_model_download_is_off(self):
        switch = next(a for a in _launch_flags() if a.startswith('--disable-features='))
        self.assertIn('OptimizationGuideOnDeviceModel', switch.split('=', 1)[1].split(','))


def _launch_flags():
    """The arguments get_webdriver hands Chrome, with no browser launched."""
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


class ExtensionLoadTest(unittest.TestCase):
    """An authenticated proxy is in force before the browser is handed over.

    Chromium 156 loads --load-extension after startup returns, so deleting the
    directory then meant the extension never ran and, with nothing else naming
    the proxy, every request went direct and reported success.
    """

    PROXY = {"url": "http://p:1", "username": "u", "password": "secret"}
    WORKER = {"type": "service_worker", "url": "chrome-extension://abc/background.js"}

    def launch(self, driver, proxy=None):
        with patch.object(utils, 'get_chrome_exe_path', return_value="/bin/chromium"), \
                patch.object(utils, 'get_chrome_major_version', return_value="151"), \
                patch.object(utils, 'start_xvfb_display'), \
                patch.object(utils, 'PATCHED_DRIVER_PATH', "/bin/chromedriver"), \
                patch.object(utils.time, 'sleep', lambda _seconds: None), \
                patch.object(geo, 'browser_language', return_value="en-US"), \
                patch.object(geo, 'browser_timezone', return_value="UTC"), \
                patch('undetected_chromedriver.Chrome', side_effect=driver):
            return utils.get_webdriver(proxy or self.PROXY)

    def test_the_directory_survives_until_the_worker_is_running(self):
        driver = _TargetsDriver([[], [], [self.WORKER]])
        self.launch(lambda options=None, **kwargs: driver.opened(options))
        self.assertEqual(driver.directory_present, [True, True, True])

    def test_the_directory_is_gone_once_the_launch_returns(self):
        driver = _TargetsDriver([[self.WORKER]])
        self.launch(lambda options=None, **kwargs: driver.opened(options))
        self.assertFalse(os.path.exists(driver.directory))

    def test_a_worker_that_never_starts_fails_the_launch(self):
        driver = _TargetsDriver([[]])
        with self.assertRaisesRegex(Exception, "proxy extension"):
            self.launch(lambda options=None, **kwargs: driver.opened(options))

    def test_a_worker_that_never_starts_closes_the_browser(self):
        driver = _TargetsDriver([[]])
        try:
            self.launch(lambda options=None, **kwargs: driver.opened(options))
        except Exception:
            pass
        self.assertTrue(driver.quit_called)

    def test_the_proxy_is_named_on_the_command_line_too(self):
        # So a late or missing extension fails on the proxy's 407 instead of
        # sending the request around it.
        driver = _TargetsDriver([[self.WORKER]])
        self.launch(lambda options=None, **kwargs: driver.opened(options))
        self.assertIn('--proxy-server=http://p:1', driver.arguments)

    def test_credentials_in_the_url_stay_off_the_command_line(self):
        driver = _TargetsDriver([[self.WORKER]])
        proxy = {"url": "http://a:b@p:1", "username": "u", "password": "secret"}
        self.launch(lambda options=None, **kwargs: driver.opened(options), proxy)
        self.assertIn('--proxy-server=http://p:1', driver.arguments)


class _TargetsDriver(_FakeDriver):
    """Answers Target.getTargets from a script, repeating the last answer."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.directory_present = []
        self.quit_called = False

    def opened(self, options):
        self.arguments = list(options.arguments)
        loaded = next(a for a in self.arguments if a.startswith('--load-extension='))
        self.directory = loaded.split('=', 1)[1]
        return self

    def execute_cdp_cmd(self, _command, _params):
        self.directory_present.append(os.path.exists(self.directory))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return {"targetInfos": answer}

    def quit(self):
        self.quit_called = True


class ExtensionScriptEscapingTest(unittest.TestCase):
    """Proxy fields from a /v1 request reach background.js as literals, never as code.

    The extension's service worker holds <all_urls> host permissions, so a
    password that closed its string literal ran script with them.
    """

    HOSTILE = 'x", y: alert(1), z: "\\'

    def literal(self, proxy, field):
        path = utils.create_proxy_extension(proxy)
        try:
            with open(os.path.join(path, "background.js")) as f:
                script = f.read()
        finally:
            shutil.rmtree(path, ignore_errors=True)
        raw = re.search(r"^\s*%s: (.*?),?$" % field, script, re.MULTILINE).group(1)
        return json.loads(raw)

    def test_each_credential_reads_back_as_the_value_sent(self):
        for field in ("username", "password"):
            with self.subTest(field=field):
                proxy = {"url": "http://p:1", "username": "u", "password": "p", field: self.HOSTILE}
                self.assertEqual(self.literal(proxy, field), self.HOSTILE)

    def test_the_host_reads_back_as_the_value_sent(self):
        proxy = {"url": 'http://ho"st:1', "username": "u", "password": "p"}
        self.assertEqual(self.literal(proxy, "host"), 'ho"st')


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
