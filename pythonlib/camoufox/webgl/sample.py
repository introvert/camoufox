import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import orjson

# Get database path relative to this file
DB_PATH = Path(__file__).parent / 'webgl_data.db'

# The probability columns of webgl_fingerprints. "android" holds Firefox for
# Android GPUs (scripts/webgl-android/build_records.py); a phone persona draws
# from it rather than from the desktop Linux pool.
WEBGL_OS_COLUMNS = ('win', 'mac', 'lin', 'android')


def sample_webgl(
    os: str, vendor: Optional[str] = None, renderer: Optional[str] = None
) -> Dict[str, str]:
    """
    Sample a random WebGL vendor/renderer combination and its data based on OS probabilities.
    Optionally use a specific vendor/renderer pair.

    Args:
        os: Operating system ('win', 'mac', 'lin', or 'android')
        vendor: Optional specific vendor to use
        renderer: Optional specific renderer to use (requires vendor to be set)

    Returns:
        Dict containing WebGL data including vendor, renderer and additional parameters

    Raises:
        ValueError: If invalid OS provided or no data found for OS/vendor/renderer
    """
    # Check that the OS is valid (avoid SQL injection)
    if os not in WEBGL_OS_COLUMNS:
        raise ValueError(f'Invalid OS: {os}. Must be one of: {", ".join(WEBGL_OS_COLUMNS)}')

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if vendor and renderer:
        # Get specific vendor/renderer pair and verify it exists for this OS
        cursor.execute(
            f'SELECT vendor, renderer, data, {os} FROM webgl_fingerprints '  # nosec
            'WHERE vendor = ? AND renderer = ?',
            (vendor, renderer),
        )
        result = cursor.fetchone()

        if not result:
            raise ValueError(f'No WebGL data found for vendor "{vendor}" and renderer "{renderer}"')

        if result[3] <= 0:  # Check OS-specific probability
            # Get a list of possible (vendor, renderer) pairs for this OS
            cursor.execute(
                f'SELECT DISTINCT vendor, renderer FROM webgl_fingerprints WHERE {os} > 0'  # nosec
            )
            possible_pairs = cursor.fetchall()
            raise ValueError(
                f'Vendor "{vendor}" and renderer "{renderer}" combination not valid for {os.title()}.\n'
                f'Possible pairs: {", ".join(str(pair) for pair in possible_pairs)}'
            )

        conn.close()
        return _without_context_attributes(orjson.loads(result[2]))

    # Get all vendor/renderer pairs and their probabilities for this OS
    # `drawable` = 0 marks GPUs that stay pinnable but are never drawn at random:
    # software rasterizers (the strongest VM/headless tell there is) and
    # renderer strings Firefox 155 never reports (no ", or similar").
    cursor.execute(
        f'SELECT vendor, renderer, data, {os} FROM webgl_fingerprints '  # nosec
        f'WHERE {os} > 0 AND drawable = 1'
    )
    results = cursor.fetchall()
    conn.close()

    if not results:
        raise ValueError(f'No WebGL data found for OS: {os}')

    # Split into separate arrays
    _, _, data_strs, probs = map(list, zip(*results))

    # Convert probabilities to numpy array and normalize
    probs_array = np.array(probs, dtype=np.float64)
    probs_array = probs_array / probs_array.sum()

    # Sample based on probabilities
    idx = np.random.choice(len(probs_array), p=probs_array)

    # Parse the JSON data string
    return _without_context_attributes(orjson.loads(data_strs[idx]))


def _without_context_attributes(data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the scraped getContextAttributes() answers.

    They are what one page asked getContext() for, not a property of the GPU,
    and the browser no longer reads them: it answers with what the page asked
    for, as Firefox does.
    """
    data.pop('webGl:contextAttributes', None)
    data.pop('webGl2:contextAttributes', None)
    return data


def has_webgl_pair(vendor: str, renderer: str) -> bool:
    """Whether webgl_data.db holds parameters for this vendor/renderer pair."""
    conn = sqlite3.connect(DB_PATH)
    try:
        return (
            conn.execute(
                'SELECT 1 FROM webgl_fingerprints WHERE vendor = ? AND renderer = ?',
                (vendor, renderer),
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def get_possible_pairs() -> Dict[str, List[Tuple[str, str]]]:
    """
    Get all possible (vendor, renderer) pairs for all OS, where the probability is greater than 0.
    """
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all vendor/renderer pairs for each OS where probability > 0
    result: Dict[str, List[Tuple[str, str]]] = {}
    for os_type in WEBGL_OS_COLUMNS:
        cursor.execute(
            'SELECT DISTINCT vendor, renderer FROM webgl_fingerprints '
            f'WHERE {os_type} > 0 ORDER BY {os_type} DESC',  # nosec
        )
        result[os_type] = cursor.fetchall()

    conn.close()
    return result


# Vendor families, matched against "<vendor> <renderer>". Order matters only
# where a string could match two (none of the bundled ones do).
_GPU_FAMILIES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ('nvidia', ('NVIDIA', 'GeForce', 'Quadro')),
    ('amd', ('AMD', 'Radeon', 'ATI ')),
    ('intel', ('Intel',)),
    ('apple', ('Apple',)),
    ('adreno', ('Adreno', 'Qualcomm')),
    ('mali', ('Mali', 'ARM')),
    ('powervr', ('PowerVR', 'Imagination')),
    ('xclipse', ('Xclipse', 'Samsung')),
)


def _gpu_family(vendor: str, renderer: str) -> Optional[str]:
    text = f'{vendor} {renderer}'
    for family, needles in _GPU_FAMILIES:
        if any(needle in text for needle in needles):
            return family
    return None


def closest_webgl_pair(os: str, vendor: str, renderer: str) -> Optional[Tuple[str, str]]:
    """The most common GPU of the same vendor family in `os`'s pool, or None.

    For a GPU webgl_data.db has no parameters for: a relative's limits and
    extensions are a far closer match than the host's own.
    """
    if os not in WEBGL_OS_COLUMNS:
        raise ValueError(f'Invalid OS: {os}. Must be one of: {", ".join(WEBGL_OS_COLUMNS)}')
    family = _gpu_family(vendor, renderer)
    if family is None:
        return None
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            f'SELECT vendor, renderer FROM webgl_fingerprints WHERE {os} > 0 AND drawable = 1 '  # nosec
            f'ORDER BY {os} DESC'
        ).fetchall()
    finally:
        conn.close()
    for row_vendor, row_renderer in rows:
        if _gpu_family(row_vendor, row_renderer) == family:
            return row_vendor, row_renderer
    return None


def webgl_record_for_pair(os: str, vendor: str, renderer: str) -> Tuple[Dict[str, Any], bool]:
    """The WebGL record for a named GPU, and whether the database knew it.

    A pair the database lacks borrows its closest relative's parameters
    (closest_webgl_pair) under its own two strings; with no relative it gets
    the strings alone.
    """
    if has_webgl_pair(vendor, renderer):
        return sample_webgl(os, vendor, renderer), True
    relative = closest_webgl_pair(os, vendor, renderer)
    if relative is None:
        return {'webGl:vendor': vendor, 'webGl:renderer': renderer, 'webGl2Enabled': True}, False
    data = sample_webgl(os, *relative)
    data['webGl:vendor'] = vendor
    data['webGl:renderer'] = renderer
    for domain in ('webGl:parameters', 'webGl2:parameters'):
        params = data.get(domain)
        if isinstance(params, dict):
            for key, value in (('7937', renderer), ('37445', vendor), ('37446', renderer)):
                if key in params:
                    params[key] = value
    return data, False
