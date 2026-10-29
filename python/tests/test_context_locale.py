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
from camoufox.fingerprints import generate_context_fingerprint


def _new_context(api, **kwargs):
    browser = mock.MagicMock()
    browser.version = "152.0.4"
    if api == "sync":
        sync_api.NewContext(browser, os="windows", **kwargs)
    else:
        context = mock.MagicMock()
        context.add_init_script = mock.AsyncMock()
        browser.new_context = mock.AsyncMock(return_value=context)
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
