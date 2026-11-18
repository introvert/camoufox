"""A context identity is data: drawn once, stored, replayed, and changed one
value at a time.

NewContext drew a fresh salt on every call (two for a locale), so no context
could be opened twice as the same identity, and a preset gave other fonts and
voices each time. Nothing could be stored, and nothing could be overridden.
"""

import asyncio
import json
import re
from unittest import mock

import pytest

from camoufox import async_api, sync_api
from camoufox.fingerprints import draw_context_identity, identity_seed, render_context_identity
from camoufox.webgl import webgl_for_gpu
from camoufox.utils import machine_from_probe
from conftest import fake_browser


def rendered(identity):
    return render_context_identity(identity, '159')


def roundtrip(identity):
    return json.loads(json.dumps(identity))


def setter_arg(script, name):
    return re.search(rf'{name}\((.+?)\);', script).group(1)


@pytest.mark.parametrize('os', ['windows', 'macos', 'linux'])
def test_a_stored_identity_replays_exactly(os):
    identity = draw_context_identity(os=os, ff_version='159')
    again = draw_context_identity(roundtrip(identity), ff_version='159')
    assert again == identity
    assert rendered(again) == rendered(identity)


def test_new_identities_differ():
    first, second = (draw_context_identity(os='windows')['salt'] for _ in range(2))
    assert first != second


def test_a_preset_is_one_identity():
    preset = {
        'navigator': {
            'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0',
            'platform': 'Win32', 'hardwareConcurrency': 8,
        },
        'screen': {'width': 1920, 'height': 1080, 'colorDepth': 24},
    }
    assert draw_context_identity(preset=preset) == draw_context_identity(preset=preset)


def test_an_override_keeps_everything_else():
    identity = draw_context_identity(os='windows')
    changed = draw_context_identity(identity, overrides={'timezone': 'Asia/Tokyo'}, i_know_what_im_doing=True)
    assert changed['config'].pop('timezone') == 'Asia/Tokyo'
    identity['config'].pop('timezone', None)
    assert changed['config'] == identity['config']


def test_another_machine_draws_its_own_values_from_the_salt():
    identity = draw_context_identity(os='windows')
    mac_ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:159.0) Gecko/20100101 Firefox/159.0'
    overrides = {'navigator.userAgent': mac_ua, 'navigator.platform': 'MacIntel'}
    first = draw_context_identity(identity, overrides=overrides, i_know_what_im_doing=True)
    second = draw_context_identity(identity, overrides=overrides, i_know_what_im_doing=True)
    assert first == second
    config = first['config']
    assert config['navigator.oscpu'] == 'Intel Mac OS X 10.15'
    assert config['navigator.appVersion'] == '5.0 (Macintosh)'
    assert config['fonts'] != identity['config']['fonts']
    assert 'Apple' in config['webGl:vendor'] or 'Intel' in config['webGl:vendor'] or 'AMD' in config['webGl:vendor']


def test_a_named_gpu_brings_its_own_tables():
    m1 = {'webGl:vendor': 'Apple', 'webGl:renderer': 'Apple M1, or similar'}
    identity = draw_context_identity(os='macos', webgl_config=('Intel Inc.', 'Intel(R) HD Graphics 400, or similar'))
    changed = draw_context_identity(identity, overrides=m1, i_know_what_im_doing=True)['config']
    reference = webgl_for_gpu('mac', *m1.values(), seed=identity_seed(changed, int(identity['salt'])))
    for key in ('webGl:supportedExtensions', 'webGl2:parameters'):
        assert changed[key] == reference[key], key


def test_another_locale_draws_the_voices_again():
    identity = draw_context_identity(os='windows', locale='en-US')
    german = draw_context_identity(identity, locale='de-DE')['config']
    assert any('German (Germany)' in v['name'] for v in german['voices'])
    assert german['fonts'] == identity['config']['fonts']
    assert german['webGl:renderer'] == identity['config']['webGl:renderer']


@pytest.mark.parametrize('key', ['navigator.doNotTrack', 'headers.Accept-Encoding', 'navigator.globalPrivacyControl'])
def test_a_browser_wide_override_raises(key):
    with pytest.raises(ValueError, match='cannot differ between contexts'):
        draw_context_identity(os='windows', overrides={key: 1})


def test_a_core_count_the_browser_does_not_have_raises():
    with pytest.raises(ValueError, match='runs on 8 cores'):
        draw_context_identity(os='windows', hardware_concurrency=8,
                              overrides={'navigator.hardwareConcurrency': 4})


@pytest.mark.parametrize('overrides', [
    {'navigator.userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0'},
    {'navigator.platform': 'Win32'},
])
def test_an_override_of_one_half_of_the_os_raises(overrides):
    identity = draw_context_identity(os='macos')
    with pytest.raises(ValueError, match='navigator.userAgent .* navigator.platform'):
        draw_context_identity(identity, overrides=overrides, i_know_what_im_doing=True)


def test_a_context_renders_at_its_own_pixel_ratio():
    identity = draw_context_identity(os='windows', overrides={'window.devicePixelRatio': 1.5},
                                     i_know_what_im_doing=True)
    assert rendered(identity)['context_options']['device_scale_factor'] == 1.5


def test_a_phone_launch_keeps_its_pixel_ratio():
    with pytest.raises(ValueError, match='renders at 2.857'):
        draw_context_identity(os='android', device_pixel_ratio=60 / 21,
                              overrides={'window.devicePixelRatio': 3}, i_know_what_im_doing=True)


def test_a_stored_identity_needs_its_own_cores():
    identity = draw_context_identity(os='windows', hardware_concurrency=8)
    with pytest.raises(ValueError, match='runs on 4 cores'):
        draw_context_identity(identity, hardware_concurrency=4)


@pytest.mark.parametrize('key, value', [('fonts', 'Arial'), ('navigator.hardwareConcurrency', 8.5)])
def test_an_override_of_another_type_raises(key, value):
    with pytest.raises(ValueError, match=key):
        draw_context_identity(os='windows', overrides={key: value}, i_know_what_im_doing=True)


def test_both_webrtc_addresses_are_set():
    identity = draw_context_identity(os='linux', overrides={'webrtc:ipv4': '203.0.113.7', 'webrtc:ipv6': '2001:db8::7'},
                                     i_know_what_im_doing=True)
    script = rendered(identity)['init_script']
    assert setter_arg(script, 'setWebRTCIPv4') == '"203.0.113.7"'
    assert setter_arg(script, 'setWebRTCIPv6') == '"2001:db8::7"'


def test_overrides_warn():
    with pytest.warns(Warning, match='drawn as a whole'):
        draw_context_identity(os='windows', overrides={'timezone': 'Europe/Paris'})


@pytest.mark.parametrize('api', ['sync', 'async'])
def test_new_context_reopens_its_identity(api):
    def open_context(**kwargs):
        browser = fake_browser('159.0', is_async=api == 'async')
        if api == 'sync':
            context = sync_api.NewContext(browser, **kwargs)
        else:
            context = asyncio.run(async_api.AsyncNewContext(browser, **kwargs))
        script = browser.new_context.return_value.add_init_script.call_args.args[0]
        return context, browser.new_context.call_args.kwargs, script

    first, options, script = open_context(os='linux', locale='fr-FR', timezone_id='Europe/Paris')
    stored = roundtrip(first.camoufox_identity)
    _, again_options, again_script = open_context(identity=stored, storage_state='state.json')
    assert again_script == script
    assert again_options == {**options, 'storage_state': 'state.json'}
    assert options['timezone_id'] == 'Europe/Paris' and options['locale'] == 'fr-FR'


def test_a_bound_browser_refuses_an_override_of_its_os():
    browser = fake_browser('159.0', host_identity=('lin', None))
    windows_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0'
    with pytest.raises(ValueError, match='canvas_noise=True'):
        sync_api.NewIdentity(browser, overrides={'navigator.userAgent': windows_ua, 'navigator.platform': 'Win32'},
                             i_know_what_im_doing=True)


def test_a_stored_identity_opens_on_a_newer_browser():
    identity = draw_context_identity(os='linux', ff_version='150')
    script = render_context_identity(identity, '160')['init_script']
    assert 'Firefox/160.0' in setter_arg(script, 'setNavigatorUserAgent')


class TestProxy:
    EXIT = mock.Mock(ip='203.0.113.7', timezone='Europe/Paris', country='FR', latitude=48.8, longitude=2.3)

    def identity(self, **kwargs):
        with mock.patch.object(sync_api, '_resolve_proxy_geo', return_value=self.EXIT):
            return sync_api.NewIdentity(fake_browser('159.0'), proxy={'server': 'http://p:1'}, **kwargs)

    def test_a_replayed_identity_keeps_its_locale_and_follows_the_exit(self):
        stored = draw_context_identity(os='linux', locale='ja-JP', timezone='Asia/Tokyo')
        config = self.identity(identity=stored)['config']
        assert config['navigator.language'] == 'ja-JP'
        assert config['timezone'] == 'Europe/Paris'
        assert config['webrtc:ipv4'] == '203.0.113.7'
        assert config['geolocation:latitude'] == 48.8

    def test_a_new_identity_speaks_the_exits_language(self):
        assert self.identity(os='linux')['config']['locale:region'] == 'FR'


class TestConnectedBrowser:
    FACTS = {
        'userAgent': 'Mozilla/5.0 (X11; Linux x86_64; rv:159.0) Gecko/20100101 Firefox/159.0',
        'cores': 6,
        'devicePixelRatio': 1,
        'covered': False,
        'gpu': ['Intel', 'Intel(R) HD Graphics, or similar'],
    }

    def test_an_uncovered_browser_binds_its_contexts_to_its_host(self):
        assert machine_from_probe(self.FACTS) == (
            ('lin', ('Intel', 'Intel(R) HD Graphics, or similar')),
            {'hardware_concurrency': 6, 'device_pixel_ratio': None},
        )

    def test_a_software_renderer_binds_the_os_only(self):
        facts = {**self.FACTS, 'gpu': ['Mesa', 'llvmpipe, or similar']}
        assert machine_from_probe(facts)[0] == ('lin', None)

    def test_a_covered_browser_binds_nothing(self):
        assert machine_from_probe({**self.FACTS, 'covered': True})[0] is None

    def test_a_phone_browser_keeps_its_ratio(self):
        facts = {**self.FACTS, 'covered': True, 'devicePixelRatio': 2.625,
                 'userAgent': 'Mozilla/5.0 (Android 16; Mobile; rv:159.0) Gecko/159.0 Firefox/159.0'}
        assert machine_from_probe(facts)[1] == {'hardware_concurrency': 6, 'device_pixel_ratio': 2.625}

    def test_contexts_opened_at_once_ask_once(self):
        browser = mock.MagicMock(spec=['version', 'new_context'])
        browser.version = '159.0'
        probe = mock.AsyncMock()

        async def evaluate(_script):
            await asyncio.sleep(0)
            return self.FACTS

        probe.new_page.return_value.evaluate.side_effect = evaluate
        context = mock.AsyncMock()
        context.add_init_script = mock.AsyncMock()
        browser.new_context = mock.AsyncMock(side_effect=[probe, context, context])

        async def open_two():
            await asyncio.gather(async_api.AsyncNewContext(browser), async_api.AsyncNewContext(browser))

        asyncio.run(open_two())
        probe.new_page.return_value.evaluate.assert_awaited_once()

    def test_it_is_asked_once(self):
        browser = mock.MagicMock(spec=['version', 'new_context'])
        browser.version = '159.0'
        probe = browser.new_context.return_value
        probe.new_page.return_value.evaluate.return_value = self.FACTS
        sync_api.NewContext(browser)
        sync_api.NewContext(browser)
        probe.new_page.return_value.evaluate.assert_called_once()
        probe.close.assert_called()
        script = browser.new_context.return_value.add_init_script.call_args.args[0]
        assert 'setNavigatorHardwareConcurrency(6)' in script
        assert 'Intel(R) HD Graphics, or similar' in script
