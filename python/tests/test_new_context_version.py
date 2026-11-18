"""A context's user agent carries the Firefox version of the browser it runs in.

Without an explicit ff_version, NewContext kept whatever version fpgen drew
(e.g. Firefox/146) on a 160 browser, so the UA disagreed with every
version-dependent API the page could probe.
"""

import asyncio
import re

import pytest

from camoufox import async_api, sync_api
from conftest import fake_browser


def _user_agent(init_script):
    return re.search(r'setNavigatorUserAgent\("([^"]+)"\)', init_script).group(1)


@pytest.mark.parametrize("api", ["sync", "async"])
def test_user_agent_matches_the_browser_version(api):
    # Playwright's Browser.version for Firefox: MOZ_APP_VERSION_DISPLAY.
    browser = fake_browser("160.0.1", is_async=api == "async")
    context = browser.new_context.return_value
    if api == "sync":
        sync_api.NewContext(browser, os="linux")
    else:
        asyncio.run(async_api.AsyncNewContext(browser, os="linux"))

    user_agent = _user_agent(context.add_init_script.call_args.args[0])
    assert "Firefox/160.0" in user_agent and "rv:160.0" in user_agent, user_agent
