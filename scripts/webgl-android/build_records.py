#!/usr/bin/env python3
"""
Build the Firefox for Android rows of pythonlib/camoufox/webgl/webgl_data.db.

webgl_data.db was scraped from desktop Firefox, so a phone persona used to get
a desktop GPU: a GTX 980 or llvmpipe with desktop limits behind an Android user
agent. Nobody runs Firefox for Android in a lab for us, so these rows are
derived instead, from two sources:

  * gles_reports.json -- real OpenGL ES driver reports (limits, extension
    strings) for the most common Android GPUs, from the OpenGL ES Hardware
    Database (opengles.gpuinfo.org, CC BY 4.0). One device per GPU bucket
    Firefox reports (see below); the weight is that bucket's share of recent
    Android 12+ reports.
  * Firefox 155's own rules for turning a GLES driver into WebGL answers,
    transcribed below with the source they come from. Firefox for Android
    runs WebGL straight on the system GLES driver (no ANGLE), so these are the
    values a page reads there.

What is modeled rather than measured, and why:
  * Shader precision formats: the database does not record them. The values
    are the ones every current mobile GPU family (Adreno, Mali, PowerVR Rogue)
    implements -- fp32 vertex shaders, fp16 fragment mediump/lowp, 16-bit
    fragment mediump/lowp ints.
  * MAX_TEXTURE_MAX_ANISOTROPY_EXT: 16 wherever the driver exposes the
    extension, which is what all of these drivers report.
  * ALIASED_POINT_SIZE_RANGE, ALIASED_LINE_WIDTH_RANGE and
    UNIFORM_BUFFER_OFFSET_ALIGNMENT are not recorded and are left out, so the
    browser answers them itself.

Run from the repo root:  python3 scripts/webgl-android/build_records.py
"""

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DB = os.path.join(REPO, 'pythonlib', 'camoufox', 'webgl', 'webgl_data.db')

# dom/canvas/SanitizeRenderer.cpp: the bucket every real GPU is collapsed to.
# gles_reports.json records the bucket next to each device.

# WebGLExtensionID order (dom/canvas/WebGLTypes.h): getSupportedExtensions()
# walks the enum, so this is the order a page sees.
EXTENSION_ORDER = [
    'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_color_buffer_float',
    'EXT_color_buffer_half_float', 'EXT_depth_clamp', 'EXT_disjoint_timer_query',
    'EXT_float_blend', 'EXT_frag_depth', 'EXT_shader_texture_lod', 'EXT_sRGB',
    'EXT_texture_compression_bptc', 'EXT_texture_compression_rgtc',
    'EXT_texture_filter_anisotropic', 'EXT_texture_norm16', 'MOZ_debug',
    'OES_draw_buffers_indexed', 'OES_element_index_uint', 'OES_fbo_render_mipmap',
    'OES_standard_derivatives', 'OES_texture_float', 'OES_texture_float_linear',
    'OES_texture_half_float', 'OES_texture_half_float_linear', 'OES_vertex_array_object',
    'OVR_multiview2', 'WEBGL_color_buffer_float', 'WEBGL_compressed_texture_astc',
    'WEBGL_compressed_texture_etc', 'WEBGL_compressed_texture_etc1',
    'WEBGL_compressed_texture_pvrtc', 'WEBGL_compressed_texture_s3tc',
    'WEBGL_compressed_texture_s3tc_srgb', 'WEBGL_debug_renderer_info',
    'WEBGL_debug_shaders', 'WEBGL_depth_texture', 'WEBGL_draw_buffers',
    'WEBGL_explicit_present', 'WEBGL_lose_context', 'WEBGL_provoking_vertex',
]


def supported_extensions(gl_exts, webgl2):
    """WebGLContext::IsExtensionSupported for an OpenGL ES 3.2 context, minus the
    ones ClientWebGLContext hides from web content by default."""
    has = set(gl_exts).__contains__
    webgl1 = not webgl2
    ok = {
        # Core in ES 3.x (GLContextFeatures.cpp); WebGL 1 only where the
        # extension's IsSupported() says so.
        'ANGLE_instanced_arrays': webgl1,
        'EXT_blend_minmax': webgl1,
        'EXT_sRGB': webgl1,
        'OES_element_index_uint': webgl1,
        'OES_fbo_render_mipmap': webgl1,
        'OES_standard_derivatives': webgl1,
        'OES_texture_float': webgl1,
        'OES_texture_half_float': webgl1,
        'OES_texture_half_float_linear': webgl1,
        'OES_vertex_array_object': webgl1,
        'WEBGL_depth_texture': webgl1,
        # renderbuffer_color_float / _half_float and frag_color_float are core
        # in ES 3.2.
        'WEBGL_color_buffer_float': webgl1,
        'EXT_color_buffer_half_float': webgl1,
        'EXT_color_buffer_float': webgl2,
        # WebGLExtensionFloatBlend: ES >= 3.2 is enough.
        'EXT_float_blend': True,
        # ESSL 1.00 extensions: Firefox test-compiles a shader that requires
        # them; drivers that advertise the GL extension pass it.
        'EXT_frag_depth': webgl1 and has('GL_EXT_frag_depth'),
        'EXT_shader_texture_lod': webgl1 and has('GL_EXT_shader_texture_lod'),
        'WEBGL_draw_buffers': webgl1 and has('GL_EXT_draw_buffers'),
        # Driver extensions.
        'EXT_depth_clamp': has('GL_EXT_depth_clamp'),
        'EXT_texture_compression_bptc': has('GL_EXT_texture_compression_bptc'),
        'EXT_texture_compression_rgtc': has('GL_EXT_texture_compression_rgtc'),
        'EXT_texture_filter_anisotropic': has('GL_EXT_texture_filter_anisotropic'),
        'OES_texture_float_linear': has('GL_OES_texture_float_linear'),
        'OES_draw_buffers_indexed': webgl2,  # draw_buffers_indexed is core in ES 3.2
        'OVR_multiview2': webgl2 and (has('GL_OVR_multiview2') or has('GL_ANGLE_multiview')),
        'WEBGL_compressed_texture_astc': has('GL_KHR_texture_compression_astc_ldr'),
        'WEBGL_compressed_texture_etc': True,  # ES3_compatibility, not ANGLE
        'WEBGL_compressed_texture_etc1': has('GL_OES_compressed_ETC1_RGB8_texture'),
        'WEBGL_compressed_texture_s3tc': has('GL_EXT_texture_compression_s3tc') or (
            has('GL_EXT_texture_compression_dxt1')
            and has('GL_ANGLE_texture_compression_dxt3')
            and has('GL_ANGLE_texture_compression_dxt5')),
        'WEBGL_compressed_texture_s3tc_srgb': has('GL_EXT_texture_compression_s3tc_srgb'),
        'WEBGL_debug_renderer_info': True,
        'WEBGL_debug_shaders': True,
        'WEBGL_lose_context': True,
        # Hidden or off on Android: privileged (EXT_disjoint_timer_query,
        # MOZ_debug), draft (EXT_texture_norm16, WEBGL_explicit_present),
        # never (WEBGL_compressed_texture_pvrtc), or not preferred on Android
        # (WEBGL_provoking_vertex).
    }
    return [name for name in EXTENSION_ORDER if ok.get(name, False)]


# GL enums (getParameter names).
P = {
    'MAX_TEXTURE_SIZE': 3379, 'MAX_VIEWPORT_DIMS': 3386, 'MAX_RENDERBUFFER_SIZE': 34024,
    'MAX_CUBE_MAP_TEXTURE_SIZE': 34076, 'MAX_VERTEX_ATTRIBS': 34921,
    'MAX_TEXTURE_IMAGE_UNITS': 34930, 'MAX_VERTEX_TEXTURE_IMAGE_UNITS': 35660,
    'MAX_COMBINED_TEXTURE_IMAGE_UNITS': 35661, 'MAX_VERTEX_UNIFORM_VECTORS': 36347,
    'MAX_VARYING_VECTORS': 36348, 'MAX_FRAGMENT_UNIFORM_VECTORS': 36349,
    'MAX_TEXTURE_MAX_ANISOTROPY_EXT': 34047, 'MAX_DRAW_BUFFERS': 34852,
    'MAX_COLOR_ATTACHMENTS': 36063,
    'MAX_3D_TEXTURE_SIZE': 32883, 'MAX_ELEMENTS_VERTICES': 33000,
    'MAX_ELEMENTS_INDICES': 33001, 'MAX_TEXTURE_LOD_BIAS': 34045,
    'MAX_ARRAY_TEXTURE_LAYERS': 35071, 'MIN_PROGRAM_TEXEL_OFFSET': 35076,
    'MAX_PROGRAM_TEXEL_OFFSET': 35077, 'MAX_VERTEX_UNIFORM_BLOCKS': 35371,
    'MAX_FRAGMENT_UNIFORM_BLOCKS': 35373, 'MAX_COMBINED_UNIFORM_BLOCKS': 35374,
    'MAX_UNIFORM_BUFFER_BINDINGS': 35375, 'MAX_UNIFORM_BLOCK_SIZE': 35376,
    'MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS': 35377,
    'MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS': 35379,
    'MAX_FRAGMENT_UNIFORM_COMPONENTS': 35657, 'MAX_VERTEX_UNIFORM_COMPONENTS': 35658,
    'MAX_VARYING_COMPONENTS': 35659,
    'MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS': 35968,
    'MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS': 35978,
    'MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS': 35979, 'MAX_SAMPLES': 36183,
    'MAX_ELEMENT_INDEX': 36203, 'MAX_SERVER_WAIT_TIMEOUT': 37137,
    'MAX_VERTEX_OUTPUT_COMPONENTS': 37154, 'MAX_FRAGMENT_INPUT_COMPONENTS': 37157,
    'VENDOR': 7936, 'RENDERER': 7937, 'UNMASKED_VENDOR_WEBGL': 37445,
    'UNMASKED_RENDERER_WEBGL': 37446,
}

# Passed straight through from glGetIntegerv / glGetInteger64v
# (WebGLContextState.cpp, WebGL2ContextState.cpp, WebGLContextValidate.cpp).
PASSTHROUGH_BOTH = [
    'MAX_TEXTURE_SIZE', 'MAX_RENDERBUFFER_SIZE', 'MAX_CUBE_MAP_TEXTURE_SIZE',
    'MAX_VERTEX_ATTRIBS', 'MAX_TEXTURE_IMAGE_UNITS', 'MAX_VERTEX_TEXTURE_IMAGE_UNITS',
    'MAX_VERTEX_UNIFORM_VECTORS', 'MAX_FRAGMENT_UNIFORM_VECTORS',
]
PASSTHROUGH_WEBGL2 = [
    'MAX_3D_TEXTURE_SIZE', 'MAX_ELEMENTS_VERTICES', 'MAX_ELEMENTS_INDICES',
    'MAX_TEXTURE_LOD_BIAS', 'MAX_ARRAY_TEXTURE_LAYERS', 'MIN_PROGRAM_TEXEL_OFFSET',
    'MAX_PROGRAM_TEXEL_OFFSET', 'MAX_VERTEX_UNIFORM_BLOCKS', 'MAX_FRAGMENT_UNIFORM_BLOCKS',
    'MAX_COMBINED_UNIFORM_BLOCKS', 'MAX_UNIFORM_BUFFER_BINDINGS', 'MAX_UNIFORM_BLOCK_SIZE',
    'MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS', 'MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS',
    'MAX_FRAGMENT_UNIFORM_COMPONENTS', 'MAX_VERTEX_UNIFORM_COMPONENTS',
    'MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS',
    'MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS',
    'MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS', 'MAX_SAMPLES', 'MAX_ELEMENT_INDEX',
    'MAX_VERTEX_OUTPUT_COMPONENTS', 'MAX_FRAGMENT_INPUT_COMPONENTS',
]

KMAX_DRAW_BUFFERS = 8  # webgl::kMaxDrawBuffers


def parameters(caps, exts, webgl2, vendor, renderer):
    def cap(name):
        return caps.get('GL_' + name)

    out = {}
    for name in PASSTHROUGH_BOTH + (PASSTHROUGH_WEBGL2 if webgl2 else []):
        value = cap(name)
        if value is not None:
            out[str(P[name])] = value
    # limits.maxTexUnits is clamped to a uint8 (WebGLContextValidate.cpp).
    if cap('MAX_COMBINED_TEXTURE_IMAGE_UNITS') is not None:
        out[str(P['MAX_COMBINED_TEXTURE_IMAGE_UNITS'])] = min(
            cap('MAX_COMBINED_TEXTURE_IMAGE_UNITS'), 255)
    # maxViewportDim = min(dims[0], dims[1]), reported for both.
    if cap('MAX_VIEWPORT_DIMS') is not None:
        out[str(P['MAX_VIEWPORT_DIMS'])] = [cap('MAX_VIEWPORT_DIMS')] * 2
    # GLES >= 3.0: MAX_VARYING_VECTORS is MAX_FRAGMENT_INPUT_COMPONENTS / 4.
    if cap('MAX_FRAGMENT_INPUT_COMPONENTS') is not None:
        out[str(P['MAX_VARYING_VECTORS'])] = cap('MAX_FRAGMENT_INPUT_COMPONENTS') // 4
    # WebGL 2: MAX_VARYING_COMPONENTS is 4 * the driver's MAX_VARYING_VECTORS.
    if webgl2 and cap('MAX_VARYING_VECTORS') is not None:
        out[str(P['MAX_VARYING_COMPONENTS'])] = 4 * cap('MAX_VARYING_VECTORS')
    # GLuint64 read through glGetInteger64v: a negative driver value wraps.
    if webgl2 and cap('MAX_SERVER_WAIT_TIMEOUT') is not None:
        out[str(P['MAX_SERVER_WAIT_TIMEOUT'])] = float(cap('MAX_SERVER_WAIT_TIMEOUT') % 2**64)
    # maxColorDrawBuffers, clamped to webgl::kMaxDrawBuffers. WebGL 1 has it
    # only through WEBGL_draw_buffers (the browser checks the extension).
    if cap('MAX_DRAW_BUFFERS') is not None and (webgl2 or 'WEBGL_draw_buffers' in exts):
        draw = min(cap('MAX_DRAW_BUFFERS'), KMAX_DRAW_BUFFERS)
        out[str(P['MAX_DRAW_BUFFERS'])] = draw
        out[str(P['MAX_COLOR_ATTACHMENTS'])] = draw
    if 'EXT_texture_filter_anisotropic' in exts:
        out[str(P['MAX_TEXTURE_MAX_ANISOTROPY_EXT'])] = 16  # modeled, see above
    out[str(P['VENDOR'])] = 'Mozilla'
    out[str(P['RENDERER'])] = renderer
    out[str(P['UNMASKED_VENDOR_WEBGL'])] = vendor
    out[str(P['UNMASKED_RENDERER_WEBGL'])] = renderer
    return out


def precision_formats():
    # Modeled, see the module docstring. Keys are "<shaderType>,<precisionType>".
    fp32 = {'rangeMin': 127, 'rangeMax': 127, 'precision': 23}
    fp16 = {'rangeMin': 15, 'rangeMax': 15, 'precision': 10}
    int32 = {'rangeMin': 31, 'rangeMax': 30, 'precision': 0}
    int16 = {'rangeMin': 15, 'rangeMax': 14, 'precision': 0}
    vertex, fragment = 35633, 35632
    low_f, med_f, high_f, low_i, med_i, high_i = range(36336, 36342)
    formats = {}
    for prec in (low_f, med_f, high_f):
        formats[f'{vertex},{prec}'] = fp32
    for prec in (low_i, med_i, high_i):
        formats[f'{vertex},{prec}'] = int32
    formats[f'{fragment},{low_f}'] = fp16
    formats[f'{fragment},{med_f}'] = fp16
    formats[f'{fragment},{high_f}'] = fp32
    formats[f'{fragment},{low_i}'] = int16
    formats[f'{fragment},{med_i}'] = int16
    formats[f'{fragment},{high_i}'] = int32
    return formats


def record(device):
    vendor = device['GL_VENDOR']
    renderer = device['bucket'] + ', or similar'
    caps, gl_exts = device['caps'], device['extensions']
    data = {'webGl:vendor': vendor, 'webGl:renderer': renderer}
    for webgl2, prefix in ((False, 'webGl'), (True, 'webGl2')):
        exts = supported_extensions(gl_exts, webgl2)
        data[f'{prefix}:supportedExtensions'] = exts
        data[f'{prefix}:parameters'] = parameters(caps, exts, webgl2, vendor, renderer)
        data[f'{prefix}:shaderPrecisionFormats'] = precision_formats()
    data['webGl2Enabled'] = True
    return vendor, renderer, data


def main():
    with open(os.path.join(HERE, 'gles_reports.json')) as f:
        reports = json.load(f)
    total = sum(d['weight'] for d in reports['devices'])

    conn = sqlite3.connect(DB)
    columns = [row[1] for row in conn.execute('PRAGMA table_info(webgl_fingerprints)')]
    if 'android' not in columns:
        conn.execute('ALTER TABLE webgl_fingerprints ADD COLUMN android REAL NOT NULL DEFAULT 0')
    for device in reports['devices']:
        vendor, renderer, data = record(device)
        conn.execute('DELETE FROM webgl_fingerprints WHERE vendor = ? AND renderer = ?',
                     (vendor, renderer))
        conn.execute(
            'INSERT INTO webgl_fingerprints (vendor, renderer, win, mac, lin, android, data) '
            'VALUES (?, ?, 0, 0, 0, ?, ?)',
            (vendor, renderer, device['weight'] / total, json.dumps(data)),
        )
        print(f"{device['weight'] / total:.3f}  {vendor} | {renderer}  ({device['report']})")
    conn.commit()
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
