@AGENTS.md

# This fork (introvert/camoufox)

AGENTS.md is upstream's (daijro/camoufox) and applies here too. What differs in
this fork:

## Syncing with upstream

Upstream is `https://github.com/daijro/camoufox`, merged in with a merge commit,
never rebased. The fork's own work lives in:

- patches upstream does not have: `mobile-meta-viewport`, `navigator-config-keys`,
  `pdf-viewer-spoofing`, `per-context-consistency`, `privacy-signals-spoofing`,
  `proxy-auth-isolation`, `webgl-headless-egl`, `webrequest-blocking-timeout`,
  `window-orientation`, `window-position-wm-placement` (daijro/camoufox#822), and
  the `mobile` additions to `force-default-pointer` and
  `touchscreen-fingerprint-spoofing`
- Juggler: mobile mode, orientation, visual-viewport input mapping, proxy auth
  isolation, the robust Promise-evaluate attach, HTTP credential arrays. Pages
  never enter Responsive Design Mode, as upstream wants, except an `isMobile`
  context with an Android user agent (`PageTarget._usesRDM`)
- `browser/settings/properties.json`: the keys those patches read stay live,
  including several upstream marked removed
- the Python launcher's Android persona (`os="android"`) and the guards in
  `browser/tests/playwright/patches/` for all of the above

After a merge, replay the stack on clean Firefox sources before anything else:
`make -C browser verify-patches` (needs the source tarball or a prepared source
dir). MaskConfig's API is upstream's: `GetBool()` is false for an unset key,
`GetOptionalBool()` tells unset from false.

## Faster iteration

```bash
CAMOUFOX_DEV_BUILD=1 make -C browser dir   # local patch work: no --enable-release (never for a shipped build)
make -C browser sync build-js              # Juggler / settings / other additions/: ./mach build faster, seconds
make -C browser build-cpp                  # C++/Rust only: ./mach build binaries
make -C browser verify-patches             # replay the stack on clean upstream files, no make dir
make -C browser verify-patches compare=true  # ...and fail if the source dir holds edits no patch records
python3 browser/scripts/patchstack.py regen browser/patches/<name>.patch  # rewrite one patch from the tree
```

- The build uses sccache when present (`mach bootstrap` installs it; it caches
  Rust too), else ccache. `CCACHE_MAXSIZE` / `SCCACHE_CACHE_SIZE` default to 20G.
  Docker keeps both caches in the `/root/.mozbuild` volume.
- `make dir` does not clobber the object directory: re-patching recompiles only
  what the patches touch. `CAMOUFOX_CLOBBER=1` restores the from-scratch reset.
- `make set-target` only rewrites the mozconfig, and the default target is the
  host.

## Releasing

Upstream's `release.yml` publishes the `camoufox` packages to PyPI and npm, so it
only runs by hand here. Browser releases come from `build.yml`: pushing a tag
builds linux and macos on x86_64 and arm64 and drafts a release, about two hours
per architecture. When the tag points at code a pull request already compiled,
which is the usual case because the release bump is the last commit on the
branch, those zips already exist as that run's artifacts:

```bash
gh workflow run copy-prebuilt-release.yml -f run_id=<pr build run> -f tag=<tag>
```

It copies them onto the release inside GitHub's network in about two minutes and
builds nothing. Check the trees first: `git rev-parse <run head>^{tree}` against
`git rev-parse <tag>^{tree}`; a merge commit has a different sha than the branch
tip it merged while holding the same tree, which is the case this is for. A pull
request builds all four zips, arm64 first, so the copy covers both architectures.

The release number in `browser/upstream.sh` continues the fork's own sequence,
not upstream's.
