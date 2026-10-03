"""
webgl_data.db holds what Firefox 155 itself reports, and nothing else.

Every drawable row is a GPU a page could meet in real Firefox: renderer strings
in SanitizeRenderer's form, no rows copied from another browser, no software
rasterizer drawn by default, no row twice, and phones only in the Android pool.

Run with:
    cd pythonlib && python -m pytest tests/test_webgl_data.py -v
"""

import sqlite3
from collections import Counter

import orjson
import pytest

from camoufox.webgl import closest_webgl_pair, sample_webgl
from camoufox.webgl.sample import DB_PATH, WEBGL_OS_COLUMNS

DESKTOP = ('win', 'mac', 'lin')


def _rows():
    conn = sqlite3.connect(DB_PATH)
    try:
        cols = ', '.join(WEBGL_OS_COLUMNS)
        rows = conn.execute(
            f'SELECT vendor, renderer, {cols}, data, drawable FROM webgl_fingerprints'
        ).fetchall()
        # A row that is never drawn at random keeps its OS weights (they say
        # which OS it can be pinned for) but counts as weightless here.
        return [row[:-1] if row[-1] else row[:2] + (0,) * len(WEBGL_OS_COLUMNS) + row[-2:-1]
                for row in rows]
    finally:
        conn.close()


def _drawable(row, pools=WEBGL_OS_COLUMNS):
    weights = dict(zip(WEBGL_OS_COLUMNS, row[2:-1]))
    return any(weights[p] > 0 for p in pools)


def test_no_pair_is_listed_twice():
    pairs = Counter((row[0], row[1]) for row in _rows())
    assert [pair for pair, n in pairs.items() if n > 1] == []


def test_every_drawable_renderer_is_in_firefox_form():
    # Firefox 155 appends ", or similar" to every renderer it sanitizes, and
    # gl.VENDOR is always "Mozilla". A row without them is another browser's
    # (the SwiftShader row was Chrome's: VENDOR "WebKit") or an old Firefox's.
    for row in _rows():
        if not _drawable(row):
            continue
        data = orjson.loads(row[-1])
        assert row[1].endswith(', or similar'), row[1]
        assert data['webGl:parameters'].get('7936') == 'Mozilla', row[1]


def test_software_rasterizers_stay_pinnable_for_their_os():
    # webgl_config=("Mesa", "llvmpipe, or similar") is documented and tested
    # (tests/patches/webgl-config-knob.py); it must keep resolving on Linux.
    assert sample_webgl('lin', 'Mesa', 'llvmpipe, or similar')['webGl:renderer'] == 'llvmpipe, or similar'


def test_software_rasterizers_are_never_drawn():
    for row in _rows():
        if any(name in row[1] for name in ('llvmpipe', 'SwiftShader', 'Basic Render Driver')):
            assert not _drawable(row), row[1]


def test_phones_only_in_the_android_pool():
    for row in _rows():
        family_is_phone = any(n in row[1] for n in ('Adreno', 'Mali', 'PowerVR')) and 'ANGLE' not in row[1]
        if family_is_phone:
            assert not _drawable(row, DESKTOP), row[1]
        if _drawable(row, ('android',)):
            assert family_is_phone, row[1]


@pytest.mark.parametrize('pool', WEBGL_OS_COLUMNS)
def test_every_pool_can_be_drawn(pool):
    data = sample_webgl(pool)
    assert data['webGl:renderer'].endswith(', or similar')
    # getContextAttributes() echoes the page's request; it is not spoofed.
    assert 'webGl:contextAttributes' not in data


def test_android_records_look_like_phones():
    for row in _rows():
        if not _drawable(row, ('android',)):
            continue
        data = orjson.loads(row[-1])
        for prefix in ('webGl', 'webGl2'):
            exts = data[f'{prefix}:supportedExtensions']
            assert 'WEBGL_compressed_texture_etc' in exts
            assert 'WEBGL_compressed_texture_astc' in exts
            assert 'WEBGL_provoking_vertex' not in exts
            fmt = data[f'{prefix}:shaderPrecisionFormats']
            assert fmt['35632,36337'] == {'rangeMin': 15, 'rangeMax': 15, 'precision': 10}
            assert fmt['35632,36338']['precision'] == 23


def test_closest_pair_stays_in_the_family_and_pool():
    vendor, renderer = closest_webgl_pair(
        'win', 'Google Inc. (Intel)', 'ANGLE (Intel, Intel(R) Arc(TM) A750 Graphics Direct3D11 vs_5_0 ps_5_0), or similar'
    )
    assert 'Intel' in renderer and renderer.startswith('ANGLE')
    assert closest_webgl_pair('android', 'Qualcomm', 'Adreno (TM) 430, or similar') == (
        'Qualcomm', 'Adreno (TM) 650, or similar'
    )
    assert closest_webgl_pair('lin', 'Other', 'Generic Renderer') is None
