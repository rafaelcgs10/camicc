"""Generate darktable styles (.dtstyle) for the headroom ICC variant.

Each style wires up the complete headroom workflow so it applies with one
click in darktable:

  input color profile -> the "(colors only, headroom)" ICC
  exposure            -> -2.0 EV, BEFORE the profile, so no highlight ever
                         reaches the LUT above 1.0 (LittleCMS would clip it)
  basic adjustments   -> +2.7 EV, restoring the level AFTER the profile
                         (basicadj sits after the input profile in the
                         default pipe order — no custom module order needed,
                         and it reproduces a post-profile exposure exactly);
                         net +0.7 EV into the tone mapper, highlights intact
  sigmoid             -> the scene-referred tone mapper, at the setting that
                         best matched Adobe/Lightroom in the test sweep
  color calibration   -> disabled (it must not adapt white balance for a DCP
                         profile, and cannot be used as a gain — enabling it
                         casts the image)
  filmic rgb / base curve -> disabled (so they don't stack a second curve)

White balance is deliberately NOT in the style: "as shot" multipliers are
per-image data. Set darktable's chromatic adaptation to legacy (or the
display-referred workflow) so the white balance module carries the full
"as shot" balance — see the README.
"""
from __future__ import annotations

import argparse
import os
import sys
from xml.sax.saxutils import escape

from . import dtparams as dp
from .cli import resolve_dcps
from .dcp import parse_dcp
from .pipeline import HEADROOM_EV

# Best sigmoid setting from the parameter sweep on the headroom variant
# (testing/sweep.py), folder-average vs a Lightroom export — the fair
# reference for a DCP conversion. Overridable with --contrast / --skew.
DEFAULT_CONTRAST = 1.95
DEFAULT_SKEW = -0.225

# net exposure into the tone mapper (darktable's usual scene-referred bump);
# the headroom EV (imported from the pipeline, so the style always matches
# the ICC that was built) is split around the input profile
NET_EV = 0.7


def _plugin(num, op, version, params, enabled, priority=0, name=''):
    return f'''  <plugin>
   <num>{num}</num>
   <module>{version}</module>
   <operation>{op}</operation>
   <op_params>{params}</op_params>
   <enabled>{enabled}</enabled>
   <blendop_version>{dp.BLENDOP_VERSION}</blendop_version>
   <blendop_params>{dp.BLEND_DEFAULT}</blendop_params>
   <multi_priority>{priority}</multi_priority>
   <multi_name>{escape(name)}</multi_name>
   <multi_name_hand_edited>0</multi_name_hand_edited>
  </plugin>'''


def build_style(style_name, icc_basename, contrast, skew,
                description=None):
    """The .dtstyle XML for one headroom profile. No custom module order is
    needed: exposure sits before the input profile and basicadj after it in
    darktable's default pipe order, which is exactly the headroom sandwich."""
    main_ev = NET_EV - HEADROOM_EV
    plugins = [
        _plugin(0, 'colorin', dp.COLORIN_VERSION,
                dp.colorin_file_params(icc_basename), 1),
        # -2.0 EV before the profile: nothing reaches the LUT above 1.0
        _plugin(1, 'exposure', dp.EXPOSURE_VERSION,
                dp.exposure_params(main_ev), 1),
        # +2.7 EV after the profile (basicadj is post-colorin by default),
        # restoring the level — net +0.7 EV into sigmoid, highlights intact
        _plugin(2, 'basicadj', dp.BASICADJ_VERSION,
                dp.basicadj_params(HEADROOM_EV), 1),
        _plugin(3, 'sigmoid', dp.SIGMOID_VERSION,
                dp.sigmoid_params(contrast=contrast, skew=skew), 1),
        # disabled so the user's auto-applied workflow modules don't fight
        # the profile / double the tone curve
        _plugin(4, 'channelmixerrgb', dp.CHMIX_VERSION, dp.CHMIX_PARAMS, 0),
        _plugin(5, 'filmicrgb', dp.FILMICRGB_VERSION, dp.FILMICRGB_PARAMS, 0),
        _plugin(6, 'basecurve', dp.BASECURVE_VERSION, dp.BASECURVE_PARAMS, 0),
    ]
    if description is None:
        description = (f'camicc headroom profile. Input profile: '
                      f'{icc_basename}. Set chromatic adaptation to legacy '
                      f'(white balance = as shot). Sigmoid contrast '
                      f'{contrast}, skew {skew}.')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<darktable_style version="1.0">\n'
            ' <info>\n'
            f'  <name>{escape(style_name)}</name>\n'
            f'  <description>{escape(description)}</description>\n'
            ' </info>\n'
            ' <style>\n'
            + '\n'.join(plugins) + '\n'
            ' </style>\n'
            '</darktable_style>\n')


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='camicc-styles',
        description='Generate darktable styles (.dtstyle) that apply the '
                    'camicc "headroom" input profile with the correct '
                    'exposure setup in one click.')
    ap.add_argument('dcp', nargs='*',
                    help='the .dcp/profile name(s) whose headroom ICC the '
                         'style should use — same lookup and wildcards as '
                         'camicc (e.g. "Canon EOS RP Camera *"). The matching '
                         'ICC must be built and installed separately with '
                         'camicc --install --variant headroom')
    ap.add_argument('-o', '--outdir', default='styles',
                    help='output directory for the .dtstyle files '
                         '(default: ./styles)')
    ap.add_argument('--contrast', type=float, default=DEFAULT_CONTRAST,
                    help=f'sigmoid contrast (default {DEFAULT_CONTRAST}, the '
                         'sweep optimum vs Lightroom)')
    ap.add_argument('--skew', type=float, default=DEFAULT_SKEW,
                    help=f'sigmoid skew (default {DEFAULT_SKEW})')
    ap.add_argument('--name', default=None,
                    help='style name (default: derived from the profile). '
                         'Only sensible with a single profile')
    a = ap.parse_args(argv)

    if not a.dcp:
        sys.exit('give at least one profile name (e.g. "Canon EOS RP '
                 'Camera Standard"); see camicc-fetch-dcps for the DCPs')

    os.makedirs(a.outdir, exist_ok=True)
    paths = []
    for arg in a.dcp:
        for p in resolve_dcps(arg):
            if p not in paths:
                paths.append(p)

    written = []
    for path in paths:
        dcp = parse_dcp(path)
        base = ' '.join(x for x in (dcp.unique_camera_model, dcp.profile_name)
                        if x) or os.path.splitext(os.path.basename(path))[0]
        icc_basename = f'{base} (colors only, headroom).icc'
        style_name = a.name or f'{base} (headroom)'
        xml = build_style(style_name, icc_basename, a.contrast, a.skew)
        out = os.path.join(a.outdir, f'{style_name}.dtstyle')
        with open(out, 'w') as f:
            f.write(xml)
        written.append(out)
        print(f'wrote {out}')

    if written:
        print(f'\nImport into darktable: lighttable > styles panel > import '
              f'(or double-click the .dtstyle). The matching ICC must be '
              f'installed first:\n'
              f'  camicc --install --variant headroom <profile>\n'
              f'Then set chromatic adaptation to legacy so white balance is '
              f'"as shot" — see the README.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
