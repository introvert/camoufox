"""
Verify the fleet-wide WebGL GPU default, and the two warnings pinning earns.

A deployment usually wants one GPU policy for every process without editing each
call site. The fingerprint config cannot come from a file: the browser reads it
as chunked CAMOU_CONFIG_<n> environment variables that launch_options() writes
fresh on every launch, so exporting CAMOU_CONFIG yourself is ignored. The
environment knob is the one place a default can live.

    export CAMOUFOX_WEBGL_CONFIG="Mesa|llvmpipe, or similar"

An explicit webgl_config argument still wins, so a caller can override the fleet.

Pinning skips sample_webgl_for_screen(), which is what normally keeps the GPU
coherent with the screen BrowserForge picked, so the two checks it would have
made are made on the pinned value instead. They warn rather than resample: the
caller named that GPU on purpose, and quietly substituting another would be
worse than saying the pairing is odd.

No browser is launched; this exercises launch_options() alone. It does still need
an installed build to resolve, since launch_options() reads properties.json from
it, so run `camoufox fetch` first.

What PASS means:
    * the environment default is picked up, and a passed argument beats it;
    * a malformed value fails loudly at launch rather than silently sampling;
    * pinning a discrete GPU behind a screen too small for one warns;
    * pinning a software rasterizer warns, since it is a VM signal applied to
      every session;
    * a preset that names its own GPU beats the fleet default, and an explicit
      argument beats the preset by replacing it rather than landing half over it,
      so the renderer string and the parameter table never name different GPUs;
    * and a sampled GPU, the default path, warns about neither.
"""

import os
import sys
import warnings
from typing import Any, Dict, List

from camoufox._warnings import LeakWarning
from camoufox.utils import launch_options, webgl_config_from_env

SOFTWARE = ("Mesa", "llvmpipe, or similar")
DISCRETE = ("NVIDIA Corporation", "NVIDIA GeForce GTX 980, or similar")


def _check(results: Dict[str, Any], label: str, got: Any, expected: Any) -> None:
    ok = got == expected
    results[label] = ok
    verdict = "PASS" if ok else "FAIL"
    suffix = "" if ok else f" (expected {expected!r})"
    print(f"  {verdict} {label:44} -> {got!r}{suffix}")


def _launch(**kwargs: Any) -> Dict[str, Any]:
    """launch_options() with the network-touching parts left alone."""
    return launch_options(os="linux", headless=True, **kwargs)


def _full(opts: Dict[str, Any]) -> Dict[str, Any]:
    """The config the browser will actually be handed."""
    import orjson

    env = opts.get("env", {})
    blob = "".join(v for k, v in sorted(env.items()) if k.startswith("CAMOU_CONFIG"))
    return orjson.loads(blob)


def _renderer(opts: Dict[str, Any]) -> Any:
    """The renderer the browser will actually be told about."""
    return _full(opts).get("webGl:renderer")


def _warnings_from(**kwargs: Any) -> List[str]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _launch(**kwargs)
    # Keep the whole message: the phrase that identifies each warning sits well
    # past the first line's opening words.
    return [" ".join(str(w.message).split()) for w in caught
            if issubclass(w.category, LeakWarning)]


def _run() -> bool:
    results: Dict[str, Any] = {}

    print("-- the environment default --")
    os.environ["CAMOUFOX_WEBGL_CONFIG"] = "Mesa|llvmpipe, or similar"
    _check(results, "parsed from the environment", webgl_config_from_env(), SOFTWARE)
    _check(results, "reaches the browser config",
           _renderer(_launch(i_know_what_im_doing=True)), SOFTWARE[1])

    print()
    print("-- an argument beats the fleet --")
    _check(results, "explicit webgl_config wins",
           _renderer(_launch(webgl_config=DISCRETE, i_know_what_im_doing=True)),
           DISCRETE[1])

    print()
    print("-- the config stays internally coherent --")
    PRESET = {"webGl:vendor": "Intel", "webGl:renderer": "Intel(R) HD Graphics 400, or similar"}
    os.environ["CAMOUFOX_WEBGL_CONFIG"] = "Mesa|llvmpipe, or similar"
    # A preset naming its own GPU is a deliberate choice; the fleet default is not.
    preset = _full(_launch(config=dict(PRESET), i_know_what_im_doing=True))
    _check(results, "a preset's GPU beats the fleet default",
           preset["webGl:renderer"], PRESET["webGl:renderer"])
    _check(results, "and its unmasked name agrees",
           preset["webGl:parameters"]["37446"], PRESET["webGl:renderer"])

    # An explicit argument does beat the preset, and must replace it rather than
    # land half over it: merge_into() keeps keys the config already holds, so the
    # strings and the parameter table could otherwise name different GPUs.
    forced = _full(_launch(config=dict(PRESET), webgl_config=SOFTWARE,
                           i_know_what_im_doing=True))
    _check(results, "an argument replaces the preset's GPU",
           forced["webGl:renderer"], SOFTWARE[1])
    _check(results, "and leaves no mismatched unmasked name",
           forced["webGl:parameters"]["37446"], SOFTWARE[1])
    del os.environ["CAMOUFOX_WEBGL_CONFIG"]

    print()
    print("-- a malformed value is not silently ignored --")
    os.environ["CAMOUFOX_WEBGL_CONFIG"] = "llvmpipe"
    raised = "<none>"
    try:
        webgl_config_from_env()
    except ValueError as exc:
        raised = "ValueError" if "Vendor|Renderer" in str(exc) else str(exc)[:40]
    _check(results, "missing separator raises", raised, "ValueError")
    del os.environ["CAMOUFOX_WEBGL_CONFIG"]

    print()
    print("-- the warnings pinning earns --")
    software = _warnings_from(webgl_config=SOFTWARE)
    _check(results, "software rasterizer warns",
           any("software rasterizer" in w for w in software), True)

    # A discrete GPU behind a netbook screen is the pairing the screen check exists
    # for; sample_webgl_for_screen() would have rejected it.
    discrete_small = _warnings_from(
        webgl_config=DISCRETE, screen=None, window=(1024, 600),
        config={"screen.width": 1024, "screen.height": 600},
    )
    _check(results, "discrete GPU on a tiny screen warns",
           any("discrete GPU" in w for w in discrete_small), True)

    sampled = _warnings_from()
    _check(results, "the sampled default warns about neither",
           [w for w in sampled if "rasterizer" in w or "discrete GPU" in w], [])

    return all(results.values())


def main() -> int:
    passed = _run()
    print()
    if passed:
        print("PASS: the WebGL GPU knob is controllable and warns where it should")
        return 0
    print("FAIL: the WebGL GPU knob does not behave as documented")
    return 1


if __name__ == "__main__":
    sys.exit(main())
