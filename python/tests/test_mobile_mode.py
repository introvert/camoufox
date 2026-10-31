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
import re
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

from camoufox import coherence, utils
from camoufox._warnings import LeakWarning
from camoufox.coherence import BROWSER_CHROME_HEIGHT, is_mobile_config, is_mobile_user_agent
from camoufox.fingerprints import (
    ANDROID_FONTS,
    MOBILE_FIREFOX_PREFS,
    MOBILE_FONT_PREFS,
    android_gpu,
    android_gpus,
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
        config, prefs = launch(config={'navigator.userAgent': ANDROID_UA})
        assert config['mobile'] is True
        assert prefs['ui.useOverlayScrollbars'] == 1

    def test_android_user_agent_without_os_gets_a_phone_navigator(self):
        # Without os=, the generator drew a random desktop OS, so the Android
        # UA sat next to platform "Win32" / "MacIntel".
        for _ in range(8):
            config, _ = launch(config={'navigator.userAgent': ANDROID_UA})
            assert config['navigator.platform'].startswith('Linux arm')
            assert config['navigator.appVersion'] == '5.0 (Android 16)'

    @pytest.mark.parametrize('ua, platform, app_version', [
        (WINDOWS_UA, 'Win32', '5.0 (Windows)'),
        (LINUX_UA, 'Linux x86_64', '5.0 (X11)'),
    ])
    def test_user_agent_decides_the_os(self, ua, platform, app_version):
        for _ in range(4):
            config, _ = launch(config={'navigator.userAgent': ua})
            assert config['navigator.platform'] == platform
            assert config['navigator.appVersion'] == app_version

    def test_user_agent_alone_still_fixes_the_drawn_platform(self):
        # A Linux draw can carry another OS's platform beside its Linux oscpu.
        # The caller set only the user agent, so platform and oscpu follow it.
        fp = generate_fingerprint(os='linux')
        fp['navigator']['platform'] = 'Win32'
        config, _ = launch(fingerprint=fp, config={'navigator.userAgent': LINUX_UA})
        assert config['navigator.platform'] == 'Linux x86_64'
        assert config['navigator.oscpu'] == 'Linux x86_64'

    def test_callers_platform_is_kept(self):
        config, _ = launch(config={'navigator.userAgent': LINUX_UA, 'navigator.platform': 'Linux i686'})
        assert config['navigator.platform'] == 'Linux i686'

    def test_user_agent_and_os_disagreeing_warns(self):
        with pytest.warns(LeakWarning, match='names a different OS'):
            config, _ = launch(os='windows', config={'navigator.userAgent': ANDROID_UA},
                               i_know_what_im_doing=False)
        assert config['navigator.platform'].startswith('Linux arm')

    def test_user_agent_in_the_os_list_is_quiet(self, recwarn):
        launch(os=['windows', 'android'], config={'navigator.userAgent': ANDROID_UA},
               i_know_what_im_doing=False)
        assert not [w for w in recwarn if 'names a different OS' in str(w.message)]

    def test_app_version_follows_the_callers_user_agent(self):
        ua = ANDROID_UA.replace('Android 16', 'Android 14')
        config, _ = launch(config={'navigator.userAgent': ua})
        assert config['navigator.appVersion'] == '5.0 (Android 14)'

    def test_callers_app_version_is_kept(self):
        config, _ = launch(config={'navigator.userAgent': ANDROID_UA, 'navigator.appVersion': '5.0 (X11)'})
        assert config['navigator.appVersion'] == '5.0 (X11)'

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
        assert prefs['dom.webmidi.enabled'] is False
        assert prefs['dom.disable_window_move_resize'] is True
        assert prefs['browser.tabs.remote.coep.credentialless'] is False
        assert prefs['browser.link.open_newwindow.restriction'] == 0
        assert prefs['dom.webserial.enabled'] is False
        assert prefs['media.getdisplaymedia.enabled'] is False

    def test_android_launch_gets_firefox_for_androids_system_colors(self):
        _, prefs = launch(os='android')
        # Fenix's colorAccent, and Highlight as that accent at alpha 78.
        assert prefs['ui.accentcolor'] == '#180e30'
        assert prefs['ui.highlight'] == 'rgba(24, 14, 48, 0.306)'
        # NS_SAME_AS_FOREGROUND_COLOR, which style resolves to currentcolor.
        assert prefs['ui.highlighttext'] == 'rgba(2, 0, 0, 0)'
        assert prefs['ui.-moz-dialog'] == '#efebe7'

    def test_android_launch_has_a_phones_media_devices(self):
        config, _ = launch(os='android')
        assert config['mediaDevices:microphoneLabels'] == ['Default audio input device']
        assert config['mediaDevices:speakerLabels'] == ['Default audio output device']
        assert config['mediaDevices:webcamLabels'] == ['Facing front:1', 'Facing back:0']
        assert config['mediaDevices:microphoneGroups'] == config['mediaDevices:speakerGroups']
        assert len(set(config['mediaDevices:webcamGroups'])) == 2

    @pytest.mark.parametrize('os_name', ['windows', 'macos', 'linux'])
    def test_desktop_media_devices_are_drawn(self, os_name):
        config, _ = launch(os=os_name)
        assert 'Default audio input device' not in config['mediaDevices:microphoneLabels']


class _FakeBrowser:
    def __init__(self):
        self.kwargs = None

    def new_page(self, **kwargs):
        self.kwargs = kwargs

    def new_context(self, **kwargs):
        self.kwargs = kwargs


def _from_options(config):
    return {'env': {'CAMOU_CONFIG_1': json.dumps(config)}}


class TestTouchDefault:
    """A mobile-mode launch's pages need has_touch for Playwright's tap()."""

    def test_launches_mobile_reads_the_config(self):
        assert utils.launches_mobile(_from_options({'mobile': True}))
        assert utils.launches_mobile(_from_options({'navigator.userAgent': ANDROID_UA}))
        assert not utils.launches_mobile(_from_options({'navigator.userAgent': LINUX_UA}))
        assert not utils.launches_mobile({})

    @pytest.mark.parametrize('factory', ['new_page', 'new_context'])
    def test_phone_pages_get_has_touch(self, factory):
        browser = _FakeBrowser()
        utils.attach_context_defaults(browser, True, True)
        getattr(browser, factory)()
        assert browser.kwargs['has_touch'] is True

    def test_caller_value_wins(self):
        browser = _FakeBrowser()
        utils.attach_context_defaults(browser, True, True)
        browser.new_page(has_touch=False)
        assert browser.kwargs['has_touch'] is False

    def test_desktop_pages_are_untouched(self):
        browser = _FakeBrowser()
        utils.attach_context_defaults(browser, True, False)
        browser.new_page()
        assert 'has_touch' not in browser.kwargs

    def test_persistent_phone_context_gets_has_touch(self):
        assert utils.persistent_context_options(_from_options({'mobile': True}), True)['has_touch'] is True
        assert 'has_touch' not in utils.persistent_context_options(_from_options({}), True)


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

    @pytest.mark.parametrize('os', ['windows', 'macos', 'linux', 'android'])
    def test_context_window_fits_its_screen(self, os):
        # Juggler sizes the real window to the viewport plus the browser's
        # chrome, and outerWidth/outerHeight report that window. A viewport of
        # the screen's full height made it taller than the screen, and the
        # launch's window size was reported beside it.
        for _ in range(20):
            fp = generate_context_fingerprint(os=os, ff_version='155', i_know_what_im_doing=True)
            config, viewport = fp['config'], fp['context_options']['viewport']
            assert viewport['width'] == config['window.outerWidth'] <= config['screen.availWidth']
            assert viewport['height'] + BROWSER_CHROME_HEIGHT == config['window.outerHeight']
            assert config['window.outerHeight'] <= config['screen.availHeight'] <= config['screen.height']
            if os != 'android':
                # A relaunch with this config reports the same page size.
                assert viewport == {'width': config['window.innerWidth'], 'height': config['window.innerHeight']}
            # Its own position, inside its screen: the launch's sat a full-width
            # context window partly off it.
            x, y = map(int, re.search(r'setWindowPosition\((-?\d+), (-?\d+)\)', fp['init_script']).groups())
            assert 0 <= x <= config['screen.width'] - config['window.outerWidth']
            assert 0 <= y <= config['screen.height'] - config['window.outerHeight']

    def test_juggler_pads_a_context_window_to_the_claimed_chrome(self):
        # Juggler sizes a context's window from its viewport; with the real
        # toolbars shorter than the claim, outer - inner fell short of it.
        juggler = Path(__file__).resolve().parents[2] / 'browser/additions/juggler/TargetRegistry.js'
        claimed = re.search(r'CLAIMED_CHROME_HEIGHT = (\d+);', juggler.read_text())
        assert claimed and int(claimed.group(1)) == BROWSER_CHROME_HEIGHT



class TestReviewFixes:
    def test_desktop_fingerprint_under_android_ua_gets_a_digitizer(self):
        config, _ = launch(fingerprint=generate_fingerprint(os='linux'), config={'navigator.userAgent': ANDROID_UA})
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
        with pytest.warns(Warning, match='Linux pool'):
            generate_context_fingerprint(os='android', ff_version='155')


class TestPhoneGpu:
    ADRENO = ('Qualcomm', 'Adreno (TM) 650, or similar')
    # A bucket Gecko's SanitizeRenderer.cpp reports that BrowserForge never recorded.
    UNRECORDED = ('Qualcomm', 'Adreno (TM) 540, or similar')

    def test_a_phone_claims_a_recorded_phone_gpu(self):
        config, prefs = launch(os='android')
        assert (config['webGl:vendor'], config['webGl:renderer']) in android_gpus()
        # The canvas renders on the host, whose GPU the identity no longer claims.
        assert utils.has_canvas_placeholder(prefs)

    def test_the_draw_follows_the_identity(self):
        assert len({android_gpu(seed) for seed in range(200)}) == len(android_gpus())
        assert all(android_gpu(seed) == android_gpu(seed) for seed in range(20))

    def test_blocked_webgl_claims_no_gpu(self):
        config, prefs = launch(os='android', block_webgl=True)
        assert 'webGl:renderer' not in config
        assert not utils.has_canvas_placeholder(prefs)

    def test_a_phone_context_claims_a_recorded_phone_gpu(self):
        fp = generate_context_fingerprint(os='android', ff_version='155', i_know_what_im_doing=True)
        assert (fp['config']['webGl:vendor'], fp['config']['webGl:renderer']) in android_gpus()

    def test_a_recorded_phone_gpu_does_not_warn(self, recwarn):
        launch(os='android', webgl_config=self.ADRENO, i_know_what_im_doing=False)
        assert not [w for w in recwarn if 'no WebGL data' in str(w.message)]

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
            launch(os='android', webgl_config=self.UNRECORDED, i_know_what_im_doing=False)

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
