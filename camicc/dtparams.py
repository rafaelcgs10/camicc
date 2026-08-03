"""darktable module parameters, shared by the test harness (testing/dtxmp.py)
and the style generator (camicc/styles.py).

darktable history/style params are binary C structs, zlib-compressed and
base64-encoded with a "gzNN" prefix (NN = expansion factor); small blobs may
also be stored as plain hex. The layouts here are hand-packed from darktable
5.4 sources and validated empirically (pixel-exact sanity renders): a version
bump upstream needs the corresponding struct updated.

Only the modules camicc needs are covered:
- colorin (v7): the ICC input profile
- exposure (v7): the pre/post scale of the headroom chain
- sigmoid (v3): the scene-referred tone mapper
- channelmixerrgb / filmicrgb / basecurve: carried DISABLED, to switch off
  whatever the user's workflow auto-applies that would fight the profile
"""
from __future__ import annotations

import base64
import math
import struct
import zlib

SIGMOID_VERSION = 3
COLORIN_VERSION = 7
EXPOSURE_VERSION = 7
BLENDOP_VERSION = 14

# default blendop params (normal blend, full opacity) — every history/style
# entry carries one
BLEND_DEFAULT = 'gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU='

# channelmixerrgb v3 "as shot in camera": carried DISABLED. It must never do
# chromatic adaptation for a DCP profile (which expects fully white-balanced
# camera RGB), and it cannot be repurposed as a gain stage — enabling it
# re-adapts the already-balanced white balance and casts the whole image.
CHMIX_VERSION = 3
CHMIX_PARAMS = 'gz04eJxjYGiwZ8AAxIqRD9iBmAmIWaDYbd8uO+sFh+30Zna7guxihMoDAKRhCIA='

# filmicrgb v6 and basecurve v6 module defaults: carried DISABLED so they do
# not stack a second tone curve on top of sigmoid (harvested from darktable
# 5.4's own auto-applied history).
FILMICRGB_VERSION = 6
FILMICRGB_PARAMS = (
    'gz02eNqbNXOy49kzXw60vp7owAAGDkD6hBMEQ8AsoBr9ZRU2IDG1yHQHruvKQHaDPUz'
    '+7BkfO2YgzQLEjFAxRiQ2DDBBaQC8LhQY')
BASECURVE_VERSION = 6
BASECURVE_PARAMS = (
    'gz09eNpjYIAAM6vnNnqyn22E9n235b6aa3cy6rVdRaK9/Y970fYf95bbMzA0QPEoGEq'
    'ADYnNhCELiVNGIAsAAkoSGQ==')

BASICADJ_VERSION = 2


def enc(raw: bytes) -> str:
    """gzNN + base64, the darktable params encoding."""
    comp = zlib.compress(raw, 9)
    factor = max(1, math.ceil(len(raw) / len(comp)))
    return 'gz%02d' % factor + base64.b64encode(comp).decode()


def sigmoid_params(contrast=1.5, skew=0.0, method=0, hue=100.0,
                   insets=(0.0, 0.0, 0.0), rotations=(0.0, 0.0, 0.0),
                   purity=0.0, base_primaries=0) -> str:
    """dt_iop_sigmoid_params_t v3 (darktable 5.4 src/iop/sigmoid.c).
    Defaults = the module defaults: contrast 1.5, skew 0, white 100,
    black 0.0152, per-channel (method 0), preserve hue 100."""
    return enc(struct.pack('<4f', contrast, skew, 100.0, 0.0152)
               + struct.pack('<i', method)
               + struct.pack('<f', hue)
               + struct.pack('<6f', insets[0], rotations[0],
                             insets[1], rotations[1],
                             insets[2], rotations[2])
               + struct.pack('<f', purity)
               + struct.pack('<i', base_primaries))


def colorin_file_params(icc: str) -> str:
    """dt_iop_colorin_params_t v7: type = FILE (0) + profile name, linear
    Rec2020 working space. `icc` is what darktable stores and looks up — a
    bare filename is resolved against the user's color/in/ folder (portable),
    a full path is used as-is (the test harness passes one in a temp dir)."""
    raw = (struct.pack('<i', 0) + icc.encode().ljust(512, b'\0')
           + struct.pack('<iiii', 0, 0, 0, 4) + b'\0' * 512)
    return enc(raw)


def exposure_params(ev: float, black: float = -0.000244140625) -> str:
    """dt_iop_exposure_params_t v7. black=0 makes it a pure multiplier (the
    headroom gain instance); the default black offset matches darktable's own
    exposure default."""
    raw = struct.pack('<iffff', 0, black, ev, 50.0, -4.0)
    return raw.hex() + '0100000001000000'


def basicadj_params(exposure: float, preserve_colors: int = 0) -> str:
    """dt_iop_basicadj_params_t v2 (darktable 5.4 src/iop/basicadj.c) as a
    pure +EV gain: everything but exposure at its neutral default. basicadj
    sits AFTER the input profile in darktable's default pipe order, so it
    restores the headroom pre-scale without any custom module order — it
    reproduces a post-colorin exposure instance pixel-for-pixel. Fields:
    black_point, exposure, hlcompr, hlcomprthresh, contrast,
    preserve_colors (enum), middle_grey, brightness, saturation, vibrance,
    clip."""
    return enc(struct.pack('<5f', 0.0, exposure, 0.0, 0.0, 0.0)
               + struct.pack('<i', preserve_colors)
               + struct.pack('<5f', 18.42, 0.0, 0.0, 0.0, 0.0))


# darktable 5.4 v5.0 pipe order (src/common/iop_order.c v50_order), used to
# build the headroom chain's custom order: a second exposure instance moved
# directly after the input color profile.
IOP_ORDER_V50 = (
    'rawprepare invert temperature rasterfile highlights cacorrect '
    'hotpixels rawdenoise demosaic denoiseprofile bilateral rotatepixels '
    'scalepixels lens cacorrectrgb hazeremoval ashift flip enlargecanvas '
    'overlay clipping liquify spots retouch exposure mask_manager tonemap '
    'toneequal crop graduatednd profile_gamma equalizer colorin '
    'channelmixerrgb diffuse censorize negadoctor blurs primaries nlmeans '
    'colorchecker defringe atrous lowpass highpass sharpen colortransfer '
    'colormapping channelmixer basicadj colorbalance colorequal '
    'colorbalancergb rgbcurve rgblevels basecurve filmic sigmoid agx '
    'filmicrgb lut3d colisa tonecurve levels shadhi zonesystem '
    'globaltonemap relight bilat colorcorrection colorcontrast velvia '
    'vibrance colorzones bloom colorize lowlight monochrome grain soften '
    'splittoning vignette colorreconstruct finalscale colorout clahe '
    'overexposed rawoverexposed dither borders watermark gamma').split()


def headroom_iop_order_list() -> str:
    """Custom iop-order list: v5.0 with 'exposure,1' (the headroom gain
    instance) inserted right after colorin."""
    parts = []
    for op in IOP_ORDER_V50:
        parts.append(f'{op},0')
        if op == 'colorin':
            parts.append('exposure,1')
    return ','.join(parts)
