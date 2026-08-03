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

import math
import struct

# the packing of colorin/exposure/sigmoid/the iop-order list is shared with
# the shipped style generator (camicc/styles.py) and lives in camicc.dtparams
from camicc.dtparams import (                                    # noqa: F401
    enc as _enc, sigmoid_params, SIGMOID_VERSION, colorin_file_params,
    exposure_params, headroom_iop_order_list, IOP_ORDER_V50,
    BLEND_DEFAULT, CHMIX_PARAMS)


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


LENS_VERSION = 10


def _lens_params() -> str:
    """dt_iop_lens_params_t v10 (darktable 5.4 src/iop/lens.cc) requesting
    the "embedded metadata" method with all corrections. has_been_set=FALSE
    makes darktable replace everything else with the per-image auto-detected
    defaults (and fall back to Lensfun when no embedded data exists), so
    this one blob works for any camera."""
    return _enc(struct.pack('<iii', 0, 7, 0)     # method=embedded, ALL, correct
                + struct.pack('<5f', 1.0, 0.0, 0.0, 0.0, 0.0)
                + struct.pack('<i', 1)           # target_geom rectilinear
                + b'\0' * 256                    # camera[128] + lens[128]
                + struct.pack('<i', 0)           # tca_override
                + struct.pack('<2f', 1.0, 1.0)   # tca_r/b
                + struct.pack('<4f', 1.0, 1.0, 1.0, 1.0)  # embedded fine-tunes
                + struct.pack('<f', 1.0)         # scale_md_v1
                + struct.pack('<i', 1)           # md_version 2
                + struct.pack('<f', 1.0)         # scale_md
                + struct.pack('<i', 0)           # has_been_set = FALSE
                + struct.pack('<3f', 0.0, 0.5, 0.5)  # manual vignette
                + struct.pack('<2f', 0.0, 0.0))  # reserved


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
# colorin v7 blob with type = enhanced camera matrix (darktable built-in)
COLORIN_STANDARD_MATRIX = 'gz48eJzjZhgFowABWAbaAaNgwAEAOQAAEA=='


TONEMAPPERS['sigmoid'] = (SIGMOID_VERSION, sigmoid_params())
SIGMOID_PRESETS = _sigmoid_presets()
LENS_PARAMS = _lens_params()


def _entry(num, op, enabled, ver, params, priority=0, name=''):
    return f'''     <rdf:li
      darktable:num="{num}"
      darktable:operation="{op}"
      darktable:enabled="{enabled}"
      darktable:modversion="{ver}"
      darktable:params="{params}"
      darktable:multi_name="{name}"
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="{priority}"
      darktable:blendop_version="14"
      darktable:blendop_params="{BLEND_DEFAULT}"/>'''


def make_xmp(raw_name: str, out_path: str, icc_path: str | None,
             tonemapper: bool, exposure_ev: float,
             tonemapper_op: str = 'sigmoid',
             tonemapper_params: tuple | None = None,
             headroom_ev: float | None = None) -> None:
    """Write an XMP sidecar. icc_path=None selects darktable's built-in
    standard (enhanced) color matrix instead of a profile file.
    tonemapper_op: which module from TONEMAPPERS to use for the tone mapper
    history entry ('sigmoid' for upstream darktable, 'agx' for spektrafilm).
    tonemapper_params: optional (version, params-blob) override for custom
    module settings (e.g. from sigmoid_params()).
    headroom_ev: for headroom ICCs — the main exposure is lowered to
    exposure_ev - headroom_ev so nothing reaches the LUT above 1.0, and a
    second exposure instance of +headroom_ev (pure gain, zero black offset)
    is moved directly after colorin via a custom iop-order list. Net
    exposure into the tone mapper stays exposure_ev. Color calibration must
    stay disabled: enabling it re-adapts the already-balanced white balance
    and casts the whole image (it cannot be used as a gain stage)."""
    colorin = (colorin_file_params(icc_path) if icc_path
               else COLORIN_STANDARD_MATRIX)
    tm_ver, tm_params = tonemapper_params or TONEMAPPERS[tonemapper_op]
    ev1 = exposure_ev if headroom_ev is None else exposure_ev - headroom_ev
    ops = [
        ('colorin', 1, 7, colorin, 0, ''),
        ('channelmixerrgb', 0, 3, CHMIX_PARAMS, 0, ''),
        (tonemapper_op, 1 if tonemapper else 0, tm_ver, tm_params, 0, ''),
        ('exposure', 1, 7, exposure_params(ev1), 0, ''),
        # lens correction like the camera JPEG (embedded metadata / Lensfun)
        ('lens', 1, LENS_VERSION, LENS_PARAMS, 0, ''),
    ]
    order_attr = 'darktable:iop_order_version="4"'
    if headroom_ev is not None:
        ops.append(('exposure', 1, 7,
                    exposure_params(headroom_ev, black=0.0), 1, 'gain'))
        order_attr = ('darktable:iop_order_version="0"\n'
                      f'   darktable:iop_order_list='
                      f'"{headroom_iop_order_list()}"')
    items = '\n'.join(_entry(i, *o) for i, o in enumerate(ops))
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
   {order_attr}>
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
