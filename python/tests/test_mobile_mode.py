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
browser/tests/playwright/patches/mobile-mode.py.

Run with:
    cd python && python -m pytest tests/test_mobile_mode.py -v
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

from camoufox import coherence, utils
from camoufox._warnings import LeakWarning
from camoufox.coherence import is_mobile_config, is_mobile_user_agent
from camoufox.fingerprints import (
    ANDROID_FONTS,
    MOBILE_FIREFOX_PREFS,
    MOBILE_FONT_PREFS,
    apply_mobile_mode,
    generate_context_fingerprint,
    generate_fingerprint,
    raise_screen_to_modern_floor,
)

# Values stock Firefox owns; launches send them only when the caller sets them.
BROWSER_OWNED_NAVIGATOR_KEYS = {
    'navigator.appCodeName',
    'navigator.appName',
    'navigator.product',
    'navigator.productSub',
    'navigator.buildID',
    'navigator.cookieEnabled',
    'navigator.onLine',
    'navigator.vendor',
}

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
    ), mock.patch.object(
        utils, 'has_display', lambda env: False
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
        assert prefs == {**MOBILE_FIREFOX_PREFS, **MOBILE_FONT_PREFS, 'layout.css.devPixelsPerPx': '2.75'}

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
        assert prefs['ui.useOverlayScrollbars'] == 0
        assert prefs['layout.css.devPixelsPerPx'] == '1.0'

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

    def test_screen_is_not_lifted_to_a_laptop(self):
        config = dict(self.PHONE)
        raise_screen_to_modern_floor(config)
        assert (config['screen.width'], config['screen.height']) == (412, 915)

    def test_a_portrait_phone_is_coherent(self):
        assert not coherence.screen_is_implausible(dict(self.PHONE))

    def test_desktop_still_gets_both(self):
        config = dict(self.PHONE, **{'navigator.userAgent': LINUX_UA})
        assert coherence.screen_is_implausible(config)
        raise_screen_to_modern_floor(config)
        assert config['screen.width'] >= 1366


class TestAndroidDraw:
    def test_a_recorded_phone(self):
        fp = generate_fingerprint(os='android')
        assert is_mobile_user_agent(fp['navigator']['userAgent'])
        assert fp['navigator']['platform'] == fp['navigator']['oscpu'] == 'Linux armv81'
        assert fp['screen']['width'] < fp['screen']['height']
        assert (fp['screen']['availWidth'], fp['screen']['availHeight']) == (
            fp['screen']['width'], fp['screen']['height']
        )
        assert fp['window']['devicePixelRatio'] > 1

    def test_a_list_with_android_draws_one_of_them(self):
        uas = {generate_fingerprint(os=['linux', 'android'])['navigator']['userAgent'] for _ in range(40)}
        assert any(is_mobile_user_agent(ua) for ua in uas)
        assert any('X11' in ua for ua in uas)


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

    def test_an_android_fingerprint_is_a_firefox_fingerprint(self):
        config, _ = launch(fingerprint=generate_fingerprint(os='android'), os='android', i_know_what_im_doing=False)
        assert config['mobile'] is True

    def test_android_user_agent_alone_switches_mobile_on(self):
        config, prefs = launch(os='linux', config={'navigator.userAgent': ANDROID_UA})
        assert config['mobile'] is True
        assert prefs['ui.useOverlayScrollbars'] == 1

    @pytest.mark.parametrize('os_name', ['windows', 'macos', 'linux'])
    def test_desktop_launch_is_untouched(self, os_name):
        config, prefs = launch(os=os_name)
        assert 'mobile' not in config
        assert not set(MOBILE_FIREFOX_PREFS) & set(prefs)
        assert 'layout.css.devPixelsPerPx' not in prefs

    @pytest.mark.parametrize('os_name', ['windows', 'macos', 'linux', 'android'])
    def test_browser_owned_navigator_values_are_firefoxs(self, os_name):
        """Stock Firefox decides these; only a caller who sets one sends it."""
        config, _ = launch(os=os_name)
        assert not BROWSER_OWNED_NAVIGATOR_KEYS & set(config)

    def test_caller_set_navigator_values_are_sent(self):
        config, _ = launch(os='android', config={'navigator.vendor': '', 'navigator.buildID': '20181001000000'})
        assert config['navigator.vendor'] == ''
        assert config['navigator.buildID'] == '20181001000000'

    def test_android_launch_gets_the_android_api_surface(self):
        _, prefs = launch(os='android')
        assert prefs['dom.webshare.enabled'] is True
        assert prefs['dom.media.webcodecs.enabled'] is False
        assert prefs['media.setsinkid.enabled'] is False
        assert prefs['webgl.default-antialias'] is False


class TestAndroidFonts:
    """Firefox for Android's fonts: Roboto by default, a fixed phone font set."""

    def test_the_set_is_renderable_on_linux(self):
        lin = set(json.loads((Path(utils.__file__).parent / 'fonts.json').read_text())['lin'])
        assert set(ANDROID_FONTS) <= lin

    def test_the_set_is_a_phones(self):
        assert {'Roboto', 'Noto Serif', 'Noto Color Emoji', 'Noto Sans CJK JP'} <= set(ANDROID_FONTS)
        # Desktop Linux families a phone does not have.
        assert not {'Ubuntu', 'DejaVu Sans', 'Liberation Sans', 'Noto Sans', 'Arimo', 'Twemoji Mozilla'} & set(ANDROID_FONTS)

    def test_android_launch_reports_the_phone_set(self):
        config, prefs = launch(os='android')
        assert config['fonts'] == ANDROID_FONTS
        assert prefs['font.default.x-western'] == 'sans-serif'
        assert prefs['font.name-list.sans-serif.x-western'].startswith('Roboto,')
        assert prefs['font.name-list.emoji'].split(', ')[1] == 'Noto Color Emoji'

    def test_caller_fonts_win(self):
        config, _ = launch(os='android', fonts=['Roboto'])
        assert config['fonts'][0] == 'Roboto'
        assert 'Noto Serif' not in config['fonts']

    @pytest.mark.parametrize('os_name', ['windows', 'macos', 'linux'])
    def test_desktop_keeps_its_default_font(self, os_name):
        _, prefs = launch(os=os_name)
        assert not set(MOBILE_FONT_PREFS) & set(prefs)

    def test_android_context_reports_the_phone_set(self):
        fp = generate_context_fingerprint(os='android', ff_version='155', i_know_what_im_doing=True)
        assert fp['config']['fonts'] == ANDROID_FONTS


class TestContextFingerprint:
    def test_android_context_asks_playwright_for_a_phone(self):
        fp = generate_context_fingerprint(os='android', ff_version='155', i_know_what_im_doing=True)
        opts = fp['context_options']
        assert is_mobile_user_agent(opts['user_agent'])
        assert opts['is_mobile'] is True
        assert opts['has_touch'] is True
        assert opts['device_scale_factor'] > 1
        assert opts['viewport']['width'] < opts['viewport']['height'] < 1366
        # Juggler derives screen.orientation from it.
        assert opts['screen']['width'] < opts['screen']['height']
        assert 'setNavigatorPlatform("Linux arm' in fp['init_script']

    def test_desktop_context_is_unchanged(self):
        opts = generate_context_fingerprint(os='windows', ff_version='155')['context_options']
        assert 'is_mobile' not in opts
        assert 'has_touch' not in opts
        assert 'device_scale_factor' not in opts
        assert 'screen' not in opts
        assert opts['viewport']['height'] >= 600



class TestReviewFixes:
    def test_desktop_fingerprint_under_android_ua_gets_a_digitizer(self):
        config, _ = launch(os='linux', config={'navigator.userAgent': ANDROID_UA})
        assert config['navigator.maxTouchPoints'] == 5

    def test_caller_touch_points_are_kept(self):
        config = {'navigator.userAgent': ANDROID_UA, 'navigator.maxTouchPoints': 1}
        apply_mobile_mode(config, {}, keep_touch_points=True)
        assert config['navigator.maxTouchPoints'] == 1

    def test_switch_keys_do_not_turn_off_the_device_fixes(self):
        with mock.patch.object(utils, 'fix_navigator_arch') as arch, mock.patch.object(
            utils, 'follow_baseline_protection'
        ) as taskbar:
            launch(os='linux', config={'navigator.plugins': True, 'screen.orientation': 'landscape-primary'})
        assert arch.called
        assert taskbar.called

    def test_old_playwright_gets_touch_without_is_mobile(self):
        with mock.patch('camoufox.pkgman._resolved_playwright_version', return_value=(1, 58, 0)):
            opts = generate_context_fingerprint(os='android', ff_version='155', i_know_what_im_doing=True)['context_options']
        assert 'is_mobile' not in opts
        assert opts['has_touch'] is True

    def test_current_playwright_gets_is_mobile(self):
        with mock.patch('camoufox.pkgman._resolved_playwright_version', return_value=(1, 62, 0)):
            opts = generate_context_fingerprint(os='android', ff_version='155', i_know_what_im_doing=True)['context_options']
        assert opts['is_mobile'] is True

    def test_android_context_warns_about_linux_resources(self):
        with pytest.warns(Warning, match='Linux pools'):
            generate_context_fingerprint(os='android', ff_version='155')


class TestPhoneGpu:
    ADRENO = ('Qualcomm', 'Adreno (TM) 650, or similar')

    def test_config_pair_unknown_to_the_database_is_kept(self):
        config, _ = launch(
            os='android',
            config={'webGl:vendor': self.ADRENO[0], 'webGl:renderer': self.ADRENO[1]},
        )
        assert (config['webGl:vendor'], config['webGl:renderer']) == self.ADRENO

    def test_webgl_config_pair_unknown_to_the_database_is_kept(self):
        config, _ = launch(os='android', webgl_config=self.ADRENO)
        assert (config['webGl:vendor'], config['webGl:renderer']) == self.ADRENO

    def test_unknown_pair_warns(self):
        with pytest.warns(LeakWarning, match='no WebGL data'):
            launch(os='android', webgl_config=self.ADRENO, i_know_what_im_doing=False)

    def test_a_desktop_still_needs_a_recorded_gpu(self):
        with pytest.raises(ValueError, match='No recorded WebGL data'):
            launch(os='linux', webgl_config=self.ADRENO)


class TestIsMobileWarning:
    """is_mobile is a phone only beside an Android UA (Juggler's own rule)."""

    class FakeBrowser:
        def new_context(self, **kwargs):
            return kwargs

    def test_android_context_is_quiet(self, recwarn):
        browser = utils.attach_desktop_only_warning(self.FakeBrowser())
        browser.new_context(is_mobile=True, user_agent=ANDROID_UA)
        assert not [w for w in recwarn if issubclass(w.category, LeakWarning)]

    def test_desktop_context_warns(self):
        browser = utils.attach_desktop_only_warning(self.FakeBrowser())
        with pytest.warns(LeakWarning, match='built for desktops'):
            browser.new_context(is_mobile=True, user_agent=WINDOWS_UA)

    def test_android_persistent_context_is_quiet(self, recwarn):
        launch(os='android', is_mobile=True, i_know_what_im_doing=False)
        assert not [w for w in recwarn if 'built for desktops' in str(w.message)]

    def test_android_context_on_a_linux_host_keeps_android(self):
        browser = mock.Mock(spec=[])
        browser._camoufox_host_identity = ('lin', None)
        assert utils.context_identity(browser, 'android', None) == ('android', None)
        with pytest.raises(ValueError, match='canvas_noise=True'):
            utils.context_identity(browser, 'windows', None)


class TestFleetGpu:
    """CAMOUFOX_WEBGL_CONFIG names one GPU for every launch."""

    GTX = ('NVIDIA Corporation', 'NVIDIA GeForce GTX 980, or similar')
    INTEL = ('Intel', 'Intel(R) HD Graphics, or similar')

    def env(self, monkeypatch, pair):
        monkeypatch.setenv(utils.WEBGL_CONFIG_ENV, '|'.join(pair))

    def test_applies_to_a_drawn_identity(self, monkeypatch):
        self.env(monkeypatch, self.GTX)
        config, _ = launch(os='linux')
        assert (config['webGl:vendor'], config['webGl:renderer']) == self.GTX

    def test_an_explicit_argument_wins(self, monkeypatch):
        self.env(monkeypatch, self.GTX)
        config, _ = launch(os='linux', webgl_config=self.INTEL)
        assert (config['webGl:vendor'], config['webGl:renderer']) == self.INTEL

    def test_the_config_gpu_wins(self, monkeypatch):
        self.env(monkeypatch, self.GTX)
        config, _ = launch(os='linux', config={'webGl:vendor': self.INTEL[0], 'webGl:renderer': self.INTEL[1]})
        assert (config['webGl:vendor'], config['webGl:renderer']) == self.INTEL

    def test_needs_an_os(self, monkeypatch):
        self.env(monkeypatch, self.GTX)
        with pytest.raises(ValueError, match='OS must be set'):
            launch()

    @pytest.mark.parametrize('raw', ['Intel', '|renderer', 'vendor|'])
    def test_malformed_raises(self, monkeypatch, raw):
        monkeypatch.setenv(utils.WEBGL_CONFIG_ENV, raw)
        with pytest.raises(ValueError, match='Vendor\\|Renderer'):
            launch(os='linux')


class TestPinnedGpu:
    def test_a_pinned_gpu_replaces_the_presets_whole(self):
        from camoufox import fingerprints

        preset = fingerprints.load_presets('155')['presets']['linux'][0]
        own = (preset['webgl']['unmaskedVendor'], preset['webgl']['unmaskedRenderer'])
        pinned = next(gpu for gpu in (TestFleetGpu.INTEL, TestFleetGpu.GTX) if gpu != own)
        config, _ = launch(os='linux', fingerprint_preset=preset, webgl_config=pinned)
        assert (config['webGl:vendor'], config['webGl:renderer']) == pinned

    def test_a_discrete_gpu_behind_a_netbook_screen_warns(self):
        with pytest.warns(LeakWarning, match='too small'):
            launch(
                os='linux',
                webgl_config=TestFleetGpu.GTX,
                config={'screen.width': 1024, 'screen.height': 600},
                i_know_what_im_doing=False,
            )
