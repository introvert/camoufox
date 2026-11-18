"""A context's locale reaches the identity NewContext draws for it.

The context's voices were drawn without its locale, so a de-DE context on
Windows listed only the en-US pack (David, Mark, Zira), where a browser launched
with de-DE lists the German one (Hedda, Katja, Stefan). And the locale may be
Playwright's language list, whose first entry is the locale
(per-context-consistency.patch).
"""

import asyncio
from unittest import mock

import pytest

from camoufox import async_api, sync_api
from conftest import fake_browser
from camoufox.fingerprints import POOL_KEYS, _load_voice_manifests, generate_context_fingerprint, voice_pool


def _new_context(api, **kwargs):
    browser = fake_browser(is_async=api == "async")
    if api == "sync":
        sync_api.NewContext(browser, os="windows", **kwargs)
    else:
        asyncio.run(async_api.AsyncNewContext(browser, os="windows", **kwargs))
    options = browser.new_context.call_args.kwargs
    script = browser.new_context.return_value.add_init_script.call_args.args[0]
    return options, script


@pytest.mark.parametrize("locale", ["de-DE", "de", "de, en-US, en"])
def test_voices_follow_the_locale(locale):
    fp = generate_context_fingerprint(os="windows", locale=locale)
    names = [voice["name"] for voice in fp["config"]["voices"]]
    assert any("German (Germany)" in name for name in names), names
    assert fp["context_options"]["locale"] == locale
    assert fp["config"]["navigator.language"] == locale.split(",")[0]


@pytest.mark.parametrize("api", ["sync", "async"])
def test_new_context_draws_for_its_locale(api):
    options, script = _new_context(api, locale="de, en-US, en")
    assert options["locale"] == "de, en-US, en"
    assert "German (Germany)" in script


@pytest.mark.parametrize('target_os', ['windows', 'macos'])
def test_context_voices_are_registered(target_os):
    """The browser registers the launch's voices and voices:pool; a context's
    list only selects among them, so a voice outside both is never shown."""
    pool = {v['voiceUri'] for v in voice_pool([POOL_KEYS[target_os]])}
    for locale in ('de-DE', 'fr-FR', 'ja-JP', 'en-GB'):
        for _ in range(5):
            fp = generate_context_fingerprint(os=target_os, locale=locale, i_know_what_im_doing=True)
            assert {v['voiceUri'] for v in fp['config']['voices']} <= pool


@pytest.mark.parametrize('target_os', ['linux', 'android'])
def test_a_speechd_context_carries_a_measured_grid(target_os):
    """speech-dispatcher's voices are a grid the browser expands, per context
    as at launch, so the context carries one of the measured grids."""
    grids = [r['speechd'] for r in _load_voice_manifests()['lin']['releases'].values()]
    fp = generate_context_fingerprint(os=target_os, i_know_what_im_doing=True)
    assert 'voices' not in fp['config']
    assert fp['config']['voices:speechd'] in grids
