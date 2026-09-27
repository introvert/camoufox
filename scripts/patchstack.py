#!/usr/bin/env python3
"""
Work on the patch stack without a full `make dir`.

    python3 scripts/patchstack.py verify [--compare]
        Replay every patch, in the order patch.py applies them, onto clean
        upstream copies of just the files the stack touches. Fails on any patch
        that does not apply (rejects), and lists the hunks that only applied
        with fuzz, as patch.py allows them too. Takes seconds, where
        `make dir` re-extracts and re-patches the whole tree. --compare also
        checks the result against the prepared source dir, byte for byte, which
        catches a tree edit that never made it into a patch.

    python3 scripts/patchstack.py regen patches/<name>.patch
        Rewrite one patch from the prepared source dir: base = clean upstream
        with every earlier patch replayed, target = the tree. Refuses when a
        later patch touches the same file, since the tree then holds both.

Clean upstream files come from the source dir's `unpatched` git tag when it
has one, otherwise straight from the Firefox source tarball.
"""

import argparse
import filecmp
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mixin import list_patches  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def upstream_version():
    env = {}
    with open(os.path.join(REPO, 'upstream.sh')) as f:
        for line in f:
            if '=' in line:
                key, value = line.strip().split('=', 1)
                env[key] = value
    return env['version'], env['release']


def source_dir() -> str:
    version, release = upstream_version()
    return os.path.join(REPO, f'camoufox-{version}-{release}')


def ordered_patches() -> List[str]:
    """The order patch.py applies them: roverfox patches last."""
    with_cwd = os.getcwd()
    os.chdir(REPO)
    try:
        patches = [os.path.join(REPO, p) for p in list_patches('patches')]
    finally:
        os.chdir(with_cwd)
    rest = [p for p in patches if 'roverfox' not in os.path.normpath(p).split(os.sep)]
    roverfox = [p for p in patches if p not in rest]
    return rest + roverfox


def sections(patch: str) -> Dict[str, str]:
    """Per-file sections of a patch, keyed by the path patch(1) edits.

    A section starts at a `diff --git` line, or at a bare `---`/`+++` header
    pair after the previous file's hunks (some patches concatenate plain
    unified diffs). The key comes from +++ (--- for a deletion), since a few
    patches carry a stale path in their `diff --git` line.
    """
    with open(patch, encoding='utf-8', errors='surrogateescape') as f:
        lines = f.read().splitlines(keepends=True)

    chunks: List[List[str]] = []
    current: List[str] = []
    seen_hunk = False
    for i, line in enumerate(lines):
        bare_header = (
            line.startswith('--- ')
            and i + 1 < len(lines)
            and lines[i + 1].startswith('+++ ')
            and seen_hunk
        )
        if line.startswith('diff --git ') or bare_header:
            if current:
                chunks.append(current)
            current, seen_hunk = [], False
        current.append(line)
        if line.startswith('@@'):
            seen_hunk = True
    if current:
        chunks.append(current)

    out: Dict[str, str] = {}
    for chunk in chunks:
        text = ''.join(chunk)
        m = (re.search(r'(?m)^\+\+\+ b/(\S+)', text)
             or re.search(r'(?m)^--- a/(\S+)', text)
             or re.match(r'diff --git a/(\S+)', text))
        if m:
            out[m.group(1)] = out.get(m.group(1), '') + text
    return out


def fetch_clean(files: List[str], dest: str) -> None:
    """Write clean upstream copies of `files` under dest. Files the upstream
    tree does not have (a patch creates them) are skipped."""
    tree = source_dir()
    if os.path.isdir(os.path.join(tree, '.git')) and subprocess.run(
        ['git', '-C', tree, 'rev-parse', '-q', '--verify', 'unpatched'],
        capture_output=True,
    ).returncode == 0:
        for f in files:
            blob = subprocess.run(['git', '-C', tree, 'show', f'unpatched:{f}'], capture_output=True)
            if blob.returncode == 0:
                path = os.path.join(dest, f)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'wb') as out:
                    out.write(blob.stdout)
        return

    version, _ = upstream_version()
    tarball = os.path.join(REPO, f'firefox-{version}.source.tar.xz')
    if not os.path.exists(tarball):
        sys.exit(f'No clean source: neither {tree} (with an `unpatched` tag) nor {tarball}. '
                 'Run `make fetch` first.')
    wanted = set(files)
    with tarfile.open(tarball) as tar:
        for member in tar:
            name = member.name.split('/', 1)[1] if '/' in member.name else ''
            if name in wanted and member.isfile():
                path = os.path.join(dest, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with tar.extractfile(member) as src, open(path, 'wb') as out:
                    out.write(src.read())


def apply(patch_text: str, root: str) -> subprocess.CompletedProcess:
    # The flags patch.py uses, so this passes and fails exactly where `make dir`
    # would. Fuzz is reported, not failed: the upstream stack already leans on it.
    return subprocess.run(
        ['patch', '-p1', '--forward', '-l', '--binary', '--no-backup-if-mismatch',
         '-r', '-', '-d', root],
        input=patch_text, text=True, capture_output=True,
    )


def verify(compare: bool) -> int:
    patches = ordered_patches()
    files = sorted({f for p in patches for f in sections(p)})
    root = tempfile.mkdtemp(prefix='patchstack-')
    fetch_clean(files, root)

    failures = 0
    for p in patches:
        result = apply(''.join(sections(p).values()), root)
        lines = result.stdout.splitlines() + result.stderr.splitlines()
        rejected = [l for l in lines if 'FAILED' in l or 'rej' in l or 'malformed' in l]
        fuzzed = [l for l in lines if 'with fuzz' in l]
        name = os.path.relpath(p, REPO)
        if result.returncode or rejected:
            failures += 1
            print(f'FAIL {name}')
            for line in rejected or lines:
                print(f'     {line}')
        elif fuzzed:
            print(f'fuzz {name}: {len(fuzzed)} hunk(s) applied with fuzz')
    print(f'{len(patches) - failures}/{len(patches)} patches apply '
          f'({len(files)} files)')

    if compare:
        tree = source_dir()
        differ = []
        for f in files:
            replayed, actual = os.path.join(root, f), os.path.join(tree, f)
            if os.path.exists(replayed) != os.path.exists(actual):
                differ.append((f, 'exists on one side only'))
            elif os.path.exists(replayed) and not filecmp.cmp(replayed, actual, shallow=False):
                differ.append((f, 'the source dir holds edits no patch records'))
        for f, why in differ:
            print(f'DIFF {f}: {why}')
        failures += len(differ)
    return 1 if failures else 0


def regen(patch_path: str) -> int:
    patches = ordered_patches()
    target = os.path.abspath(patch_path)
    if target not in patches:
        sys.exit(f'{patch_path} is not in the patch stack')
    files = list(sections(target))
    idx = patches.index(target)
    for later in patches[idx + 1:]:
        clash = set(files) & set(sections(later))
        if clash:
            sys.exit(f'{os.path.relpath(later, REPO)} also touches {sorted(clash)}; '
                     'the tree holds both, so regenerate by hand')

    base = tempfile.mkdtemp(prefix='patchstack-')
    fetch_clean(files, base)
    for p in patches[:idx]:
        keep = [s for f, s in sections(p).items() if f in files]
        if keep:
            result = apply(''.join(keep), base)
            if result.returncode:
                sys.exit(f'{os.path.relpath(p, REPO)} does not apply: {result.stdout}{result.stderr}')

    tree = source_dir()
    out = ''
    for f in files:
        old = os.path.join(base, f) if os.path.exists(os.path.join(base, f)) else '/dev/null'
        new = os.path.join(tree, f) if os.path.exists(os.path.join(tree, f)) else '/dev/null'
        # --no-color / --no-ext-diff: a user's color.ui=always or diff.external
        # would otherwise end up inside the patch.
        d = subprocess.run(
            ['git', '-c', 'core.quotepath=off', 'diff', '--no-index', '--no-color',
             '--no-ext-diff', '--diff-algorithm=patience', '-U6', old, new],
            capture_output=True, text=True,
        ).stdout
        d = re.sub(r'(?m)^diff --git .*$', f'diff --git a/{f} b/{f}', d, count=1)
        d = re.sub(r'(?m)^--- (?!/dev/null).*$', f'--- a/{f}', d, count=1)
        d = re.sub(r'(?m)^\+\+\+ (?!/dev/null).*$', f'+++ b/{f}', d, count=1)
        d = re.sub(r'(?m)^index .*\n', '', d)
        out += d
    with open(target, 'w', encoding='utf-8') as fh:
        fh.write(out)
    print(f'regenerated {os.path.relpath(target, REPO)} ({len(files)} files)')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('verify')
    v.add_argument('--compare', action='store_true')
    r = sub.add_parser('regen')
    r.add_argument('patch')
    args = parser.parse_args()
    return verify(args.compare) if args.cmd == 'verify' else regen(args.patch)


if __name__ == '__main__':
    sys.exit(main())
