#!/usr/bin/env python3
"""
Copy additions/ and the settings files into a prepared source dir, writing
only files whose contents changed.

copy-additions.sh rewrites every file, so each run gives all of them fresh
timestamps and the next `mach build` regenerates its backend and recompiles
everything that includes them. Leaving unchanged files alone keeps an
iteration on one Juggler file to one changed file.

    python3 scripts/sync-additions.py <source-dir>
"""

import filecmp
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# settings/ files copy-additions.sh places in the source dir's lw/.
SETTINGS = {
    'settings/camoufox.cfg': 'lw/camoufox.cfg',
    'settings/distribution/policies.json': 'lw/policies.json',
    'settings/defaults/pref/local-settings.js': 'lw/local-settings.js',
    'settings/chrome.css': 'lw/chrome.css',
    'settings/properties.json': 'lw/properties.json',
}


def copy_if_changed(src: str, dst: str) -> bool:
    if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def main() -> int:
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        sys.exit(__doc__)
    tree = os.path.abspath(sys.argv[1])
    changed = []

    additions = os.path.join(REPO, 'additions')
    for root, _, files in os.walk(additions):
        for name in files:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, additions)
            if copy_if_changed(src, os.path.join(tree, rel)):
                changed.append(rel)

    for src, rel in SETTINGS.items():
        if copy_if_changed(os.path.join(REPO, src), os.path.join(tree, rel)):
            changed.append(rel)

    for rel in changed:
        print(f'updated {rel}')
    print(f'{len(changed)} file(s) changed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
