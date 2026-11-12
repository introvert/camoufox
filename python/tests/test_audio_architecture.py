"""An identity claims a machine of the host's CPU architecture.

Stock Firefox renders an OfflineAudioContext one way on x86-64 and another on
ARM64, whatever the OS (audio-is-not-noised in ci/tribal-rules.yml), and the
render is not spoofed. So a Mac drawn on an x86 host is an Intel Mac, one drawn
on an ARM host an Apple Silicon Mac, and an identity no host of this
architecture can be (a phone on x86, Windows on ARM) warns.
"""

import warnings

import pytest

from camoufox import coherence, fingerprints, utils
from camoufox._warnings import LeakWarning, WARNINGS_DATA
from camoufox.webgl import sample_webgl_for_screen

DRAWS = 60


@pytest.fixture
def launch(monkeypatch):
    """launch_options on a Linux host of a given architecture; returns (config, warning texts)."""
    monkeypatch.setattr(utils, "_host_os_key", lambda: "lin")
    monkeypatch.setattr(utils, "add_default_addons", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils, "installed_verstr", lambda: "159.0-beta.43")
    monkeypatch.setattr(utils, "validate_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils, "launch_path", lambda: "/test/camoufox")
    monkeypatch.setattr(utils, "get_screen_cons", lambda *args, **kwargs: None)
    seen = {}

    def capture(config, *args, **kwargs):
        seen["config"] = dict(config)
        return {}

    monkeypatch.setattr(utils, "get_env_vars", capture)

    def run(arch, **kwargs):
        monkeypatch.setattr(utils, "host_architecture", lambda: arch)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            utils.launch_options(headless=True, **kwargs)
        return seen["config"], [str(w.message) for w in caught if issubclass(w.category, LeakWarning)]

    return run


def _warned(texts):
    return any(WARNINGS_DATA["audio_architecture"] in text for text in texts)


@pytest.mark.parametrize("seed", range(DRAWS))
def test_x86_draws_only_intel_macs(seed):
    gpu = sample_webgl_for_screen("mac", 1920, 1080, seed=seed, cores=8, arch="x86")
    assert coherence.gpu_architecture(gpu["webGl:vendor"], gpu["webGl:renderer"]) == "x86"


@pytest.mark.parametrize("seed", range(DRAWS))
def test_arm_draws_only_apple_silicon_macs(seed):
    gpu = sample_webgl_for_screen("mac", 1512, 982, seed=seed, cores=10, arch="arm")
    assert "Apple M" in gpu["webGl:renderer"]


@pytest.mark.parametrize("seed", range(DRAWS))
def test_x86_linux_never_draws_a_raspberry_pi(seed):
    # fpgen's Broadcom records carry an x86_64 user agent, which no Pi sends.
    gpu = sample_webgl_for_screen("lin", 1920, 1080, seed=seed, cores=8, arch="x86")
    assert gpu["webGl:vendor"] != "Broadcom"


def test_windows_on_arm_keeps_the_recorded_gpus():
    # fpgen records no Windows on ARM machine, so there is nothing to narrow to.
    gpu = sample_webgl_for_screen("win", 1920, 1080, seed=1, cores=8, arch="arm")
    assert coherence.gpu_architecture(gpu["webGl:vendor"], gpu["webGl:renderer"]) == "x86"


def test_an_intel_mac_draw_has_an_intel_mac_screen():
    for _ in range(DRAWS):
        fp = fingerprints.generate_fingerprint(os="macos", arch="x86")
        assert (fp["screen"]["width"], fp["screen"]["height"]) not in coherence.APPLE_SILICON_PANELS


@pytest.mark.parametrize("arch", ["x86", "arm"])
def test_a_mac_launch_matches_the_host(launch, arch):
    for _ in range(10):
        config, texts = launch(arch, os="macos")
        assert coherence.identity_architecture(config) == arch
        assert not _warned(texts)


def test_a_phone_on_x86_warns(launch):
    _, texts = launch("x86", os="android")
    assert _warned(texts)


def test_a_phone_on_arm_does_not_warn(launch):
    _, texts = launch("arm", os="android")
    assert not _warned(texts)


def test_windows_on_arm_warns(launch):
    _, texts = launch("arm", os="windows")
    assert _warned(texts)


def test_windows_on_x86_does_not_warn(launch):
    _, texts = launch("x86", os="windows")
    assert not _warned(texts)
