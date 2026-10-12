"""The launcher gives a launch that builds fontconfig's cache a longer timeout."""

import os

from camoufox.utils import FIRST_LAUNCH_TIMEOUT_MS, with_first_launch_timeout


def _options(tmp_path):
    conf = tmp_path / 'fonts.conf'
    conf.write_text('<fontconfig/>')
    return {'env': {'FONTCONFIG_FILE': str(conf), 'XDG_CACHE_HOME': str(tmp_path / 'cache')}}


def _write_cache(tmp_path, mtime_offset):
    cache_dir = tmp_path / 'cache' / 'fontconfig'
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / 'abc-le64.cache-9'
    cache.write_bytes(b'')
    since = os.path.getmtime(tmp_path / 'fonts.conf')
    os.utime(cache, (since + mtime_offset, since + mtime_offset))


def test_cold_cache_gets_the_first_launch_timeout(tmp_path):
    options = _options(tmp_path)
    assert with_first_launch_timeout(options)['timeout'] == FIRST_LAUNCH_TIMEOUT_MS
    assert 'timeout' not in options


def test_cache_older_than_the_font_configuration_is_cold(tmp_path):
    options = _options(tmp_path)
    _write_cache(tmp_path, -60)
    assert with_first_launch_timeout(options)['timeout'] == FIRST_LAUNCH_TIMEOUT_MS


def test_warm_cache_keeps_playwrights_timeout(tmp_path):
    options = _options(tmp_path)
    _write_cache(tmp_path, 60)
    assert with_first_launch_timeout(options) is options


def test_caller_timeout_wins(tmp_path):
    options = {**_options(tmp_path), 'timeout': 1234}
    assert with_first_launch_timeout(options)['timeout'] == 1234


def test_no_font_configuration_keeps_playwrights_timeout():
    assert 'timeout' not in with_first_launch_timeout({'env': {}})
