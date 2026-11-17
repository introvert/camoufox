import pytest


@pytest.fixture(autouse=True)
def no_host_gpu_probe(monkeypatch):
    """launch_options launches the browser to read the host's GPU when an
    identity claims the host's OS. This suite has no browser, and its results
    must not depend on the machine running it, so the host reads as having no
    WebGL. Tests of the probe's consumers patch it themselves."""
    from camoufox import utils

    monkeypatch.setattr(utils, 'host_gpu', lambda *args: None)


def fake_browser(version='152.0.4', machine=None, host_identity=None, is_async=False):
    """A Browser stand-in for NewContext: a launch's NewBrowser records its
    machine and host identity on the browser, so nothing is probed."""
    from unittest import mock

    browser = mock.MagicMock()
    browser.version = version
    browser._camoufox_machine = machine or {}
    browser._camoufox_host_identity = host_identity
    if is_async:
        context = mock.MagicMock()
        context.add_init_script = mock.AsyncMock()
        browser.new_context = mock.AsyncMock(return_value=context)
    return browser
