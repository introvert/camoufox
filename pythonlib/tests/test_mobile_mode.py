"""
Mobile mode: a profile that claims Firefox for Android has to behave like it.

Google (and anything else that serves a mobile layout) picks the HTML from the
User-Agent header, then its scripts ask the browser: `(pointer: coarse)`,
`'ontouchstart' in window`, the viewport, the device pixel ratio. A desktop
build with only the UA swapped answers every one of those like a desktop, and
gets a different page than real Firefox for Android does.

These tests cover the Python half: that os="android" (or an Android UA the
caller passed) produces a phone-shaped config, flips the `mobile` switch the
browser reads, and that desktop launches are untouched. The browser half is
tests/patches/mobile-mode.py.

Run with:
    cd pythonlib && python -m pytest tests/test_mobile_mode.py -v
"""

import json
from contextlib import contextmanager
from unittest import mock

import pytest

from camoufox import utils
from camoufox.fingerprints import (
    apply_mobile_mode,
    fix_screen_no_taskbar,
    generate_context_fingerprint,
    is_mobile_config,
    is_mobile_user_agent,
    raise_screen_to_modern_floor,
)

ANDROID_UA = 'Mozilla/5.0 (Android 16; Mobile; rv:155.0) Gecko/155.0 Firefox/155.0'
WINDOWS_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0'
LINUX_UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:155.0) Gecko/20100101 Firefox/155.0'


@contextmanager
def host():
    """Run launch_options() without a browser install, a network or addons."""
    with mock.patch.object(utils, 'installed_verstr', lambda: '155.0.1'), mock.patch.object(
        utils, 'launch_path', lambda **kwargs: '/nonexistent/camoufox'
    ), mock.patch.object(
        utils, 'get_env_vars', lambda config, *a, **k: {'CAMOU_TEST_CONFIG': json.dumps(config)}
    ), mock.patch.object(
        utils, 'validate_config', lambda *a, **k: None
    ), mock.patch.object(
        utils, 'add_default_addons', lambda *a, **k: None
    ), mock.patch.object(
        utils, 'get_screen_cons', lambda headless: None
    ):
        yield


def launch(**kwargs):
    kwargs.setdefault('i_know_what_im_doing', True)
    kwargs.setdefault('headless', True)
    with host():
        options = utils.launch_options(**kwargs)
    return json.loads(options['env']['CAMOU_TEST_CONFIG']), options['firefox_user_prefs']


class TestUserAgentDetection:
    def test_firefox_for_android_is_mobile(self):
        assert is_mobile_user_agent(ANDROID_UA)
        assert is_mobile_user_agent(
            'Mozilla/5.0 (Android 14; Tablet; rv:155.0) Gecko/155.0 Firefox/155.0'
        )

    @pytest.mark.parametrize('ua', [WINDOWS_UA, LINUX_UA, '', None])
    def test_desktop_is_not(self, ua):
        assert not is_mobile_user_agent(ua)

    def test_explicit_key_beats_the_user_agent(self):
        assert not is_mobile_config({'mobile': False, 'navigator.userAgent': ANDROID_UA})
        assert is_mobile_config({'mobile': True, 'navigator.userAgent': WINDOWS_UA})


class TestApplyMobileMode:
    def test_turns_on_the_switch_and_prefs(self):
        config = {'navigator.userAgent': ANDROID_UA}
        prefs = {}
        assert apply_mobile_mode(config, prefs, 2.75)
        assert config['mobile'] is True
        assert prefs == {'ui.useOverlayScrollbars': 1, 'layout.css.devPixelsPerPx': '2.75'}

    def test_desktop_is_left_alone(self):
        config = {'navigator.userAgent': WINDOWS_UA}
        prefs = {}
        assert not apply_mobile_mode(config, prefs, 2.75)
        assert 'mobile' not in config
        assert prefs == {}

    def test_caller_values_win(self):
        config = {'navigator.userAgent': ANDROID_UA}
        prefs = {'ui.useOverlayScrollbars': 0, 'layout.css.devPixelsPerPx': '1.0'}
        apply_mobile_mode(config, prefs, 3.0)
        assert prefs == {'ui.useOverlayScrollbars': 0, 'layout.css.devPixelsPerPx': '1.0'}

    def test_no_ratio_no_pref(self):
        prefs = {}
        apply_mobile_mode({'mobile': True}, prefs, None)
        assert 'layout.css.devPixelsPerPx' not in prefs


class TestDesktopGeometryFixesSkipPhones:
    PHONE = {
        'navigator.userAgent': ANDROID_UA,
        'screen.width': 412,
        'screen.height': 915,
        'screen.availWidth': 412,
        'screen.availHeight': 915,
    }

    def test_no_taskbar_is_carved_out(self):
        config = dict(self.PHONE)
        fix_screen_no_taskbar(config, 'lin')
        assert config['screen.availHeight'] == 915

    def test_screen_is_not_lifted_to_a_laptop(self):
        config = dict(self.PHONE)
        raise_screen_to_modern_floor(config)
        assert (config['screen.width'], config['screen.height']) == (412, 915)

    def test_desktop_still_gets_both(self):
        config = dict(self.PHONE, **{'navigator.userAgent': LINUX_UA})
        raise_screen_to_modern_floor(config)
        fix_screen_no_taskbar(config, 'lin')
        assert config['screen.width'] >= 1366
        assert config['screen.availHeight'] < config['screen.height']


class TestLaunchOptions:
    def test_android_is_an_accepted_os(self):
        utils.check_valid_os('android')
        utils.check_valid_os(['windows', 'android'])

    def test_android_launch_is_a_phone(self):
        config, prefs = launch(os='android')
        assert config['mobile'] is True
        assert is_mobile_user_agent(config['navigator.userAgent'])
        assert 'rv:155.0' in config['navigator.userAgent']
        assert config['navigator.platform'].startswith('Linux arm')
        assert config['navigator.appVersion'].startswith('5.0 (Android')
        assert config['navigator.maxTouchPoints'] > 0
        assert config['screen.width'] < config['screen.height'] < 1366
        assert config['screen.availHeight'] == config['screen.height']
        assert prefs['ui.useOverlayScrollbars'] == 1
        assert float(prefs['layout.css.devPixelsPerPx']) > 1

    def test_headful_on_a_real_display_keeps_one_dppx(self):
        _, prefs = launch(os='android', headless=False)
        assert 'layout.css.devPixelsPerPx' not in prefs

    def test_virtual_display_gets_the_phone_ratio(self):
        _, prefs = launch(os='android', headless=False, virtual_display=':99')
        assert float(prefs['layout.css.devPixelsPerPx']) > 1

    def test_android_user_agent_alone_switches_mobile_on(self):
        config, prefs = launch(os='linux', config={'navigator.userAgent': ANDROID_UA})
        assert config['mobile'] is True
        assert prefs['ui.useOverlayScrollbars'] == 1

    @pytest.mark.parametrize('os_name', ['windows', 'macos', 'linux'])
    def test_desktop_launch_is_untouched(self, os_name):
        config, prefs = launch(os=os_name)
        assert 'mobile' not in config
        assert 'ui.useOverlayScrollbars' not in prefs
        assert 'layout.css.devPixelsPerPx' not in prefs


class TestContextFingerprint:
    def test_android_context_asks_playwright_for_a_phone(self):
        fp = generate_context_fingerprint(os='android', ff_version='155')
        opts = fp['context_options']
        assert is_mobile_user_agent(opts['user_agent'])
        assert opts['is_mobile'] is True
        assert opts['has_touch'] is True
        assert opts['device_scale_factor'] > 1
        assert opts['viewport']['width'] < opts['viewport']['height'] < 1366
        assert 'setNavigatorPlatform("Linux arm' in fp['init_script']

    def test_desktop_context_is_unchanged(self):
        opts = generate_context_fingerprint(os='windows', ff_version='155')['context_options']
        assert 'is_mobile' not in opts
        assert 'has_touch' not in opts
        assert 'device_scale_factor' not in opts
        assert opts['viewport']['height'] >= 600

