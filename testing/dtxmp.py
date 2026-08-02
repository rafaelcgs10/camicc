"""Generate darktable XMP sidecars with a controlled module stack.

darktable history params are binary structs, zlib-compressed and base64
encoded with a "gzNN" prefix (NN = expansion factor). We only pin the modules
that matter for profile testing and let darktable defaults drive the rest
(demosaic, highlights, rawprepare, orientation, ...):

- colorin: the ICC under test (or darktable's built-in standard matrix)
- channelmixerrgb (color calibration): disabled -- the DCP-derived profile
  expects fully white-balanced camera RGB, so the legacy white balance
  module must do the full job (pass --conf
  plugins/darkroom/chromatic-adaptation=legacy to darktable-cli)
- exposure: fixed EV (0 for "camera look" profiles, darktable's usual +0.7
  for scene-referred tone mapping)
- tone mapper: on or off, selectable module: `sigmoid` (upstream darktable,
  params = the module defaults of darktable 5.4) or `agx` (scene-referred
  default of the spektrafilm darktable fork).
"""
from __future__ import annotations

import base64
import math
import struct
import zlib


SIGMOID_VERSION = 3


def sigmoid_params(contrast=1.5, skew=0.0, method=0, hue=100.0,
                   insets=(0.0, 0.0, 0.0), rotations=(0.0, 0.0, 0.0),
                   purity=0.0, base_primaries=0) -> str:
    """Encoded dt_iop_sigmoid_params_t v3 blob (darktable 5.4
    src/iop/sigmoid.c). Defaults = the module defaults: contrast 1.5, skew 0,
    white 100, black 0.0152, per-channel (method 0; 1 = RGB ratio), preserve
    hue 100, primaries attenuation/rotation/purity 0, base = work profile."""
    return _enc(struct.pack('<4f', contrast, skew, 100.0, 0.0152)
                + struct.pack('<i', method)
                + struct.pack('<f', hue)
                + struct.pack('<6f', insets[0], rotations[0],
                              insets[1], rotations[1],
                              insets[2], rotations[2])
                + struct.pack('<f', purity)
                + struct.pack('<i', base_primaries))


def _sigmoid_presets():
    """darktable 5.4's built-in sigmoid presets (src/iop/sigmoid.c
    init_presets), as {name: params-kwargs}."""
    d = math.radians
    return {
        'scene-referred default': {},
        'neutral gray': dict(contrast=1.22, skew=0.65),
        'ACES 100-nit like': dict(contrast=1.6, skew=-0.2, hue=0.0),
        'Reinhard': dict(contrast=1.0, skew=0.0, method=1, hue=0.0),
        'smooth': dict(contrast=1.5, skew=-0.2, hue=0.0,
                       insets=(0.1, 0.1, 0.15),
                       rotations=(d(2.0), d(-1.0), d(-3.0)),
                       purity=0.0, base_primaries=1),
    }


AGX_PARAMS = (
    'gz02eJxjYACBBnsYnjVTEkgrHGRguOBw9swZ21Nq0vZvAi3sGBgcHBjg4IC9sXEwUJ4HSazB'
    'ngnKYrs5zY6LWdB2X2aLneOeNXuq3oraqas07oXYwcCw9MEUsLzfnjawPNsSa1uIPAMDAH/A'
    'JGU='
)

# tone mapper module -> (modversion, params blob)
TONEMAPPERS = {
    'agx': (7, AGX_PARAMS),
    'sigmoid': None,  # filled below, needs _enc
}
# channelmixerrgb v3, "as shot in camera" params, module disabled in history
CHMIX_PARAMS = 'gz04eJxjYGiwZ8AAxIqRD9iBmAmIWaDYbd8uO+sFh+30Zna7guxihMoDAKRhCIA='
# colorin v7 blob with type = enhanced camera matrix (darktable built-in)
COLORIN_STANDARD_MATRIX = 'gz48eJzjZhgFowABWAbaAaNgwAEAOQAAEA=='
BLEND_DEFAULT = 'gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU='


def _enc(raw: bytes) -> str:
    comp = zlib.compress(raw, 9)
    factor = max(1, math.ceil(len(raw) / len(comp)))
    return 'gz%02d' % factor + base64.b64encode(comp).decode()


TONEMAPPERS['sigmoid'] = (SIGMOID_VERSION, sigmoid_params())
SIGMOID_PRESETS = _sigmoid_presets()


def colorin_file_params(icc_path: str) -> str:
    """colorin v7: type=FILE + profile path + linear Rec2020 working space."""
    raw = (struct.pack('<i', 0) + icc_path.encode().ljust(512, b'\0')
           + struct.pack('<iiii', 0, 0, 0, 4) + b'\0' * 512)
    return _enc(raw)


def exposure_params(ev: float) -> str:
    raw = struct.pack('<iffff', 0, -0.000244140625, ev, 50.0, -4.0)
    return raw.hex() + '0100000001000000'


def _entry(num, op, enabled, ver, params):
    return f'''     <rdf:li
      darktable:num="{num}"
      darktable:operation="{op}"
      darktable:enabled="{enabled}"
      darktable:modversion="{ver}"
      darktable:params="{params}"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="{BLEND_DEFAULT}"/>'''


def make_xmp(raw_name: str, out_path: str, icc_path: str | None,
             tonemapper: bool, exposure_ev: float,
             tonemapper_op: str = 'sigmoid',
             tonemapper_params: tuple | None = None) -> None:
    """Write an XMP sidecar. icc_path=None selects darktable's built-in
    standard (enhanced) color matrix instead of a profile file.
    tonemapper_op: which module from TONEMAPPERS to use for the tone mapper
    history entry ('sigmoid' for upstream darktable, 'agx' for spektrafilm).
    tonemapper_params: optional (version, params-blob) override for custom
    module settings (e.g. from sigmoid_params())."""
    colorin = (colorin_file_params(icc_path) if icc_path
               else COLORIN_STANDARD_MATRIX)
    tm_ver, tm_params = tonemapper_params or TONEMAPPERS[tonemapper_op]
    ops = [
        ('colorin', 1, 7, colorin),
        ('channelmixerrgb', 0, 3, CHMIX_PARAMS),
        (tonemapper_op, 1 if tonemapper else 0, tm_ver, tm_params),
        ('exposure', 1, 7, exposure_params(exposure_ev)),
    ]
    items = '\n'.join(_entry(i, op, en, ver, p)
                      for i, (op, en, ver, p) in enumerate(ops))
    xmp = f'''<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:darktable="http://darktable.sf.net/"
   xmpMM:DerivedFrom="{raw_name}"
   darktable:xmp_version="5"
   darktable:raw_params="0"
   darktable:auto_presets_applied="1"
   darktable:history_end="{len(ops)}"
   darktable:iop_order_version="4">
   <darktable:masks_history>
    <rdf:Seq/>
   </darktable:masks_history>
   <darktable:history>
    <rdf:Seq>
{items}
    </rdf:Seq>
   </darktable:history>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
'''
    with open(out_path, 'w') as f:
        f.write(xmp)
