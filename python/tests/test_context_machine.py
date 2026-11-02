"""A context claims what its browser has one of.

The browser runs on one machine: a page can time parallel workers to count its
cores, and a phone launch renders at one pixel ratio. NewContext drew both per
context, so a context reported cores a page could measure as the launch's, and
a phone context measured its text at the launch's pixel ratio beside another
device's devicePixelRatio.
"""

import asyncio
from unittest import mock

import pytest

from camoufox import async_api, sync_api
from camoufox.fingerprints import generate_context_fingerprint


def _new_context(api, machine, **kwargs):
    browser = mock.MagicMock()
    browser.version = "152.0.4"
    browser._camoufox_machine = machine
    if api == "sync":
        sync_api.NewContext(browser, **kwargs)
    else:
        context = mock.MagicMock()
        context.add_init_script = mock.AsyncMock()
        browser.new_context = mock.AsyncMock(return_value=context)
        asyncio.run(async_api.AsyncNewContext(browser, **kwargs))
    options = browser.new_context.call_args.kwargs
    script = browser.new_context.return_value.add_init_script.call_args.args[0]
    return options, script


@pytest.mark.parametrize("api", ["sync", "async"])
@pytest.mark.parametrize("os", ["windows", "macos", "linux"])
def test_a_context_reports_the_browsers_cores(api, os):
    for _ in range(5):
        _, script = _new_context(api, {"hardware_concurrency": 6}, os=os)
        assert "setNavigatorHardwareConcurrency(6)" in script


@pytest.mark.parametrize("api", ["sync", "async"])
def test_a_phone_context_is_the_launch_device(api):
    options, _ = _new_context(api, {"device_pixel_ratio": 60 / 21}, os="android",
                              i_know_what_im_doing=True)
    assert options["device_scale_factor"] == 60 / 21
    assert options["screen"] == {"width": 378, "height": 811}


def test_no_phone_renders_at_a_desktop_ratio():
    with pytest.raises(ValueError, match="pixel ratio of 1"):
        generate_context_fingerprint(os="android", device_pixel_ratio=1, i_know_what_im_doing=True)


def test_a_context_identity_is_as_coherent_as_a_launch():
    # A context drew its own GPU but skipped the whole-identity repairs a launch
    # applies, so an Apple Silicon context reported the drawn colour depth of 24
    # where a relaunch with its identity reports 30.
    for _ in range(20):
        script = generate_context_fingerprint(
            os="macos", webgl_config=("Apple", "Apple M1, or similar"))["init_script"]
        assert "setScreenColorDepth(30)" in script
