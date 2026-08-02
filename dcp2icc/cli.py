"""Command line interface for dcp2icc."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .dcp import parse_dcp
from .pipeline import render_clut
from .icc import write_icc


def default_dcp_dirs():
    """Folders searched for DCPs given by name: $DCP2ICC_DCP_DIR (when set
    it REPLACES the others, scoping bulk operations), else ./dcps (as
    populated by dcp2icc-fetch-dcps) and ~/.cache/dcp2icc/dcps."""
    env = os.environ.get('DCP2ICC_DCP_DIR')
    if env:
        return [env] if os.path.isdir(env) else []
    dirs = ['dcps', os.path.expanduser('~/.cache/dcp2icc/dcps')]
    return [d for d in dirs if os.path.isdir(d)]


def resolve_dcp(path):
    """The given file — or, when it does not exist, a file with that name
    anywhere inside the default DCP folders (case-insensitive, '.dcp'
    appended when missing)."""
    if os.path.isfile(path):
        return path
    name = os.path.basename(path)
    if not name.lower().endswith('.dcp'):
        name += '.dcp'
    for base in default_dcp_dirs():
        for root, _, files in os.walk(base):
            for f in files:
                if f.lower() == name.lower():
                    return os.path.join(root, f)
    sys.exit(f'{path}: no such file, and no "{name}" found in the default '
             'DCP folders ($DCP2ICC_DCP_DIR, ./dcps, ~/.cache/dcp2icc/dcps).'
             ' Populate them with dcp2icc-fetch-dcps, which downloads Adobe '
             'DNG Converter and extracts its profiles.')


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='dcp2icc',
        description='Convert DNG camera profiles (.dcp) to darktable-ready '
                    'ICC input profiles, keeping the HueSatMap/LookTable '
                    'color tables and tone curve that define the camera look.')
    ap.add_argument('dcp', nargs='*',
                    help='input .dcp file(s); a bare name (e.g. "Canon EOS '
                         'RP Camera Standard") is looked up in the default '
                         'DCP folders (populate them with dcp2icc-fetch-dcps).'
                         ' With no argument, EVERY .dcp found in the default '
                         'folders is converted — scope that with '
                         '$DCP2ICC_DCP_DIR (e.g. dcps/Camera/<your camera>) '
                         'or the full ~4,400-profile tree will be converted')
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

    inputs = a.dcp
    bulk = not inputs
    if bulk:
        inputs = sorted(
            os.path.join(root, f)
            for base in default_dcp_dirs()
            for root, _, files in os.walk(base)
            for f in files if f.lower().endswith('.dcp'))
        if not inputs:
            sys.exit('no DCPs given and none found in the default DCP '
                     'folders ($DCP2ICC_DCP_DIR, ./dcps, '
                     '~/.cache/dcp2icc/dcps) — populate them with '
                     'dcp2icc-fetch-dcps, or pass files explicitly')
        print(f'no DCP given — converting all {len(inputs)} profiles from '
              'the default DCP folders')

    written = []
    for path in inputs:
        path = resolve_dcp(path)
        try:
            dcp = parse_dcp(path)
        except ValueError as e:
            if not bulk:
                raise
            print(f'skipping {path}: {e}', file=sys.stderr)
            continue
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
            try:
                if kind == 'look':
                    lab, itab = render_clut(
                        dcp, grid=a.grid, hsm_illuminant=a.hsm_illuminant,
                        curve='custom' if curve_data else 'dcp',
                        curve_mode=a.curve_mode, curve_data=curve_data)
                else:
                    lab, itab = render_clut(
                        dcp, grid=a.grid, hsm_illuminant=a.hsm_illuminant,
                        curve='none', pre_ev=0.0)
            except ValueError as e:
                if not bulk:
                    raise
                print(f'skipping {name}: {e}', file=sys.stderr)
                continue
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
