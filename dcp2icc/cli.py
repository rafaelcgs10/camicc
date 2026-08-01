"""Command line interface for dcp2icc."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .dcp import parse_dcp
from .pipeline import render_clut
from .icc import write_icc


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='dcp2icc',
        description='Convert DNG camera profiles (.dcp) to darktable-ready '
                    'ICC input profiles, keeping the HueSatMap/LookTable '
                    'color tables and tone curve that define the camera look.')
    ap.add_argument('dcp', nargs='+', help='input .dcp file(s)')
    ap.add_argument('-o', '--outdir', default='.', help='output directory')
    ap.add_argument('--variant', choices=['look', 'colors', 'both'], default='both',
                    help='"look" bakes in the DCP tone curve (use with the tone '
                         'mapper disabled in darktable); "colors" is color-only '
                         'for the scene-referred workflow (default: both)')
    ap.add_argument('--name', default=None,
                    help='profile name prefix (default: camera + profile name '
                         'from the DCP)')
    ap.add_argument('--grid', type=int, default=33, help='CLUT grid size (default 33)')
    ap.add_argument('--curve-mode', choices=['channel', 'luminance'], default='channel',
                    help='tone curve application: per RGB channel (camera-like, '
                         'default) or luminance-only (hue preserving)')
    ap.add_argument('--custom-curve', default=None,
                    help='JSON file [[x...],[yr...],[yg...],[yb...]] with per-channel '
                         'sRGB curves; overrides the DCP curve for the look variant')
    ap.add_argument('--hsm-illuminant', type=int, choices=[1, 2], default=2,
                    help='HueSatMap table to use for dual-illuminant DCPs '
                         '(1 = tungsten, 2 = daylight, default 2)')
    ap.add_argument('--install', action='store_true',
                    help='also copy the ICCs into ~/.config/darktable/color/in')
    a = ap.parse_args(argv)

    os.makedirs(a.outdir, exist_ok=True)
    install_dir = os.path.expanduser('~/.config/darktable/color/in')
    if a.install:
        os.makedirs(install_dir, exist_ok=True)

    curve_data = None
    if a.custom_curve:
        import numpy as np
        raw = json.load(open(a.custom_curve))
        curve_data = (np.array(raw[0]), (np.array(raw[1]), np.array(raw[2]), np.array(raw[3])))

    written = []
    for path in a.dcp:
        dcp = parse_dcp(path)
        base = a.name or ' '.join(x for x in (dcp.unique_camera_model, dcp.profile_name) if x)
        base = base or os.path.splitext(os.path.basename(path))[0]

        variants = []
        if a.variant in ('look', 'both'):
            if dcp.tone_curve is not None or curve_data is not None:
                variants.append(('look', f'{base} (camera look)'))
            elif a.variant == 'look':
                print(f'{path}: no tone curve in DCP and no --custom-curve; '
                      f'skipping look variant', file=sys.stderr)
        if a.variant in ('colors', 'both'):
            variants.append(('colors', f'{base} (colors only)'))

        for kind, name in variants:
            if kind == 'look':
                lab, itab = render_clut(
                    dcp, grid=a.grid, hsm_illuminant=a.hsm_illuminant,
                    curve='custom' if curve_data else 'dcp',
                    curve_mode=a.curve_mode, curve_data=curve_data)
            else:
                lab, itab = render_clut(
                    dcp, grid=a.grid, hsm_illuminant=a.hsm_illuminant,
                    curve='none', pre_ev=0.0)
            out = os.path.join(a.outdir, f'{name}.icc')
            write_icc(out, name, lab, itab, a.grid,
                      copyright_=f'Derived from "{os.path.basename(path)}" '
                                 f'({dcp.copyright})')
            written.append(out)
            print(f'wrote {out}')
            if a.install:
                import shutil
                shutil.copy(out, install_dir)
                print(f'  installed into {install_dir}')

    if a.install and written:
        print('\nRestart darktable, then select the profile in the '
              '"input color profile" module.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
