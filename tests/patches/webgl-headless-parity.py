"""
Verify that WebGL looks the same headless as it does headful.

Firefox decides whether WebGL exists from gfxInfo, and gfxInfo gets its answer
from the glxtest probe, which opens an X connection before it does anything
else. Headless has no X server, so the probe dies with "Unable to open a
connection to the X server", GfxInfo blocks most features by default, and the
X11_EGL feature is force-disabled because the probe never reported EGL. UseEGL
then stays false, GLContextProviderLinux::CreateHeadless() picks GLX, GLX wants
the display that is missing, and canvas.getContext('webgl') returns null.

None of Camoufox's WebGL spoofing applies to that. The spoof layer lives inside
ClientWebGLContext -- parameters, extensions, precision formats, context
attributes -- so it only ever rewrites a context that was created. With no
context there is nothing to rewrite, and a browser that claims a GPU in every
other signal while having no WebGL at all is a louder tell than any renderer
string would be.

patches/webgl-headless-egl.patch takes the EGL provider directly when headless
and stops a display-less probe from vetoing WebGL. EGL reaches Mesa's swrast
device, or EGL_PLATFORM_SURFACELESS_MESA where there is no render node, and
either gives a real GL ES context on llvmpipe with no X server and no hardware.

Needs Xvfb, for the headful half. Also needs Mesa's EGL (libegl1 and
libegl-mesa0 on Debian and Ubuntu): libxul dlopens libEGL.so.1 rather than
linking it, so without it the browser still starts and only WebGL fails.

Run against a specific build:
    CAMOUFOX_EXECUTABLE_PATH=/path/to/camoufox-bin python tests/patches/webgl-headless-parity.py
(without the env var it uses the camoufox-managed browser download.)

What PASS means:
    * both modes hand the page a real context, for WebGL 1 and WebGL 2;
    * both compile, link and draw, and read back the colour the shader wrote;
    * and every value a fingerprinter can read -- vendor, renderer, both
      unmasked strings, limits, extension lists, context attributes, shader
      precision formats -- is identical between the two modes.

The framebuffer hash is reported rather than asserted. Camoufox does not spoof
rendered pixels, so on a machine with a real GPU the headful half draws with it
while headless draws with llvmpipe, and the hashes differ for a reason this
patch does not address.
"""

import asyncio
import json
import os
import sys
from typing import Any, Dict, Optional

from camoufox.async_api import AsyncCamoufox

EXECUTABLE_PATH = os.environ.get("CAMOUFOX_EXECUTABLE_PATH")

# Read back in the page's own world. The isolated world sees typed arrays
# through Xrays, which refuses access to their data, so readPixels() and
# MAX_VIEWPORT_DIMS are unreachable from page.evaluate().
PROBE = r"""
(function () {
  var A = function (v) {
    try { return Array.prototype.slice.call(v); } catch (e) { return String(v); }
  };
  var out = {};
  ['webgl', 'webgl2'].forEach(function (type) {
    var c = document.createElement('canvas');
    c.width = 256; c.height = 256;
    var reason = null;
    c.addEventListener('webglcontextcreationerror', function (e) {
      reason = e.statusMessage || '(empty)';
    });
    var gl = null;
    try { gl = c.getContext(type, {preserveDrawingBuffer: true}); }
    catch (e) { reason = 'threw: ' + e.message; }
    if (!gl) { out[type] = {context: null, creationError: reason}; return; }

    var dbg = gl.getExtension('WEBGL_debug_renderer_info');
    var r = {
      context: gl.constructor.name,
      VERSION: gl.getParameter(gl.VERSION),
      GLSL: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
      VENDOR: gl.getParameter(gl.VENDOR),
      RENDERER: gl.getParameter(gl.RENDERER),
      UNMASKED_VENDOR: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
      UNMASKED_RENDERER: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
      MAX_TEXTURE_SIZE: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      MAX_RENDERBUFFER_SIZE: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
      MAX_VIEWPORT_DIMS: A(gl.getParameter(gl.MAX_VIEWPORT_DIMS)),
      ALIASED_LINE_WIDTH_RANGE: A(gl.getParameter(gl.ALIASED_LINE_WIDTH_RANGE)),
      extensions: (gl.getSupportedExtensions() || []).slice().sort(),
      attrs: gl.getContextAttributes()
    };
    var p = gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT);
    r.FRAG_HIGH_FLOAT = p ? [p.rangeMin, p.rangeMax, p.precision] : null;

    // Draw, then read it back: a context that reports well but renders nothing
    // is worse than no context at all.
    var vs = gl.createShader(gl.VERTEX_SHADER);
    gl.shaderSource(vs, 'attribute vec2 p; void main(){ gl_Position = vec4(p,0.0,1.0); }');
    gl.compileShader(vs);
    var fs = gl.createShader(gl.FRAGMENT_SHADER);
    gl.shaderSource(fs, 'precision mediump float; void main(){ gl_FragColor = vec4(0.2,0.7,0.4,1.0); }');
    gl.compileShader(fs);
    r.vsOk = gl.getShaderParameter(vs, gl.COMPILE_STATUS);
    r.fsOk = gl.getShaderParameter(fs, gl.COMPILE_STATUS);
    var pr = gl.createProgram();
    gl.attachShader(pr, vs); gl.attachShader(pr, fs); gl.linkProgram(pr);
    r.linkOk = gl.getProgramParameter(pr, gl.LINK_STATUS);
    gl.useProgram(pr);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-0.8,-0.8, 0.8,-0.8, 0.0,0.8]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(pr, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    var px = new Uint8Array(4);
    gl.readPixels(128, 100, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    r.centerPixel = A(px);
    r.glError = gl.getError();

    var all = new Uint8Array(256 * 256 * 4);
    gl.readPixels(0, 0, 256, 256, gl.RGBA, gl.UNSIGNED_BYTE, all);
    var h = 2166136261;
    for (var i = 0; i < all.length; i++) { h ^= all[i]; h = Math.imul(h, 16777619); }
    r.fbHash = (h >>> 0).toString(16);

    out[type] = r;
  });
  document.documentElement.setAttribute('data-webgl', JSON.stringify(out));
})();
"""

# The shader writes vec4(0.2, 0.7, 0.4, 1.0); these are those floats as bytes.
EXPECTED_PIXEL = [51, 178, 102, 255]

# Reported side by side but never asserted: Camoufox does not spoof the rendered
# image, so these track whichever rasterizer each mode happened to get.
UNASSERTED = ("fbHash",)


def _launch_kwargs(headless: Any) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = dict(headless=headless, os="linux")
    if EXECUTABLE_PATH:
        kwargs["executable_path"] = EXECUTABLE_PATH
    return kwargs


def _check(results: Dict[str, Any], label: str, got: Any, expected: Any) -> None:
    ok = got == expected
    results[label] = ok
    verdict = "PASS" if ok else "FAIL"
    suffix = "" if ok else f" (expected {expected!r})"
    print(f"  {verdict} {label:46} -> {got!r}{suffix}")


async def _probe(headless: Any) -> Dict[str, Any]:
    async with AsyncCamoufox(**_launch_kwargs(headless)) as browser:
        page = await browser.new_page()
        await page.goto("data:text/html,<html><body>probe</body></html>")
        await page.evaluate(
            "(src) => { const s = document.createElement('script');"
            " s.textContent = src; document.body.appendChild(s); }",
            PROBE,
        )
        raw = await page.get_attribute("html", "data-webgl")
    if not raw:
        raise RuntimeError("probe wrote nothing")
    return json.loads(raw)


def _describe_missing(mode: str, probe: Dict[str, Any]) -> None:
    for kind in ("webgl", "webgl2"):
        entry = probe.get(kind, {})
        if entry.get("context") is None:
            print(f"    {mode} {kind}: no context -- {entry.get('creationError')}")


async def _run() -> bool:
    results: Dict[str, Any] = {}

    print("-- collecting --")
    headless = await _probe(True)
    print("  headless probed")
    virtual = await _probe("virtual")
    print("  headful (virtual display) probed")
    print()

    print("-- a context exists at all --")
    for kind in ("webgl", "webgl2"):
        _check(results, f"headful {kind} context",
               virtual.get(kind, {}).get("context") is not None, True)
        _check(results, f"headless {kind} context",
               headless.get(kind, {}).get("context") is not None, True)
    _describe_missing("headless", headless)
    _describe_missing("headful", virtual)
    print()

    if not all(results.values()):
        # Comparing parameters of a context that does not exist says nothing.
        print("-- parity -- skipped, a context is missing above")
        return False

    print("-- it actually renders --")
    for mode, probe in (("headless", headless), ("headful", virtual)):
        for kind in ("webgl", "webgl2"):
            r = probe[kind]
            _check(results, f"{mode} {kind} compiles and links",
                   [r["vsOk"], r["fsOk"], r["linkOk"]], [True, True, True])
            _check(results, f"{mode} {kind} draws the shader colour",
                   r["centerPixel"], EXPECTED_PIXEL)
    print()

    print("-- every value a page can read is identical --")
    for kind in ("webgl", "webgl2"):
        for key in sorted(headless[kind]):
            if key in UNASSERTED:
                continue
            _check(results, f"{kind}.{key}", headless[kind][key], virtual[kind][key])
    print()

    print("-- reported, not asserted --")
    for kind in ("webgl", "webgl2"):
        h, v = headless[kind]["fbHash"], virtual[kind]["fbHash"]
        note = "match" if h == v else "differ; the rendered image is not spoofed"
        print(f"  {kind} framebuffer hash: headless {h}, headful {v} ({note})")

    return all(results.values())


async def main() -> int:
    try:
        passed = await _run()
    except Exception as exc:  # noqa: BLE001 -- the message is the diagnostic
        name = type(exc).__name__
        if "Xvfb" in name:
            print(f"FAIL: this test needs Xvfb for the headful half ({name})")
            return 1
        raise
    print()
    if passed:
        print("PASS: WebGL is present and identical in both modes")
        return 0
    print("FAIL: headless WebGL differs from headful")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
