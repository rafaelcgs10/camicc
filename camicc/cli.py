"""Command line interface for camicc."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys

from .dcp import parse_dcp
from .pipeline import (render_clut, illuminant_dependent,
                       HEADROOM_EV, HEADROOM_GRID)
from .icc import write_icc

VARIANT_SETS = {
    'look': {'look'},
    'colors': {'colors'},
    'headroom': {'headroom'},
    'both': {'look', 'colors'},               # pre-headroom behavior
    'all': {'look', 'colors', 'headroom'},
}


def default_dcp_dirs():
    """Folders searched for DCPs given by name: $CAMICC_DCP_DIR (when set
    it REPLACES the others, scoping bulk operations), else ./dcps (as
    populated by camicc-fetch-dcps) and ~/.cache/camicc/dcps."""
    env = (os.environ.get('CAMICC_DCP_DIR')
           or os.environ.get('DCP2ICC_DCP_DIR'))    # deprecated name
    if env:
        return [env] if os.path.isdir(env) else []
    dirs = ['dcps', os.path.expanduser('~/.cache/camicc/dcps'),
            os.path.expanduser('~/.cache/dcp2icc/dcps')]  # deprecated
    return [d for d in dirs if os.path.isdir(d)]


def resolve_dcps(path):
    """The given file — or files with that name anywhere inside the
    default DCP folders (case-insensitive, '.dcp' appended when missing).
    The name may contain shell-style wildcards: "Canon EOS RP Camera *"
    matches every Camera-style profile of that model. Returns a list."""
    if os.path.isfile(path):
        return [path]
    name = os.path.basename(path)
    if not name.lower().endswith('.dcp'):
        name += '.dcp'
    pattern = name.lower()
    hits = []
    for base in default_dcp_dirs():
        for root, _, files in os.walk(base):
            for f in sorted(files):
                if fnmatch.fnmatch(f.lower(), pattern):
                    hits.append(os.path.join(root, f))
    if hits:
        return sorted(set(hits))
    sys.exit(f'{path}: no such file, and nothing matching "{name}" in the '
             'default DCP folders ($CAMICC_DCP_DIR, ./dcps, '
             '~/.cache/camicc/dcps). Populate them with camicc-fetch-dcps, '
             'which downloads Adobe DNG Converter and extracts its '
             'profiles.')


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='camicc',
        description='Convert DNG camera profiles (.dcp) to darktable-ready '
                    'ICC input profiles, keeping the HueSatMap/LookTable '
                    'color tables and tone curve that define the camera look.')
    ap.add_argument('dcp', nargs='*',
                    help='input .dcp file(s); a bare name (e.g. "Canon EOS '
                         'RP Camera Standard") is looked up in the default '
                         'DCP folders (populate them with camicc-fetch-dcps) '
                         'and may contain wildcards ("Canon EOS RP Camera *" '
                         'converts all Camera-style profiles of that model). '
                         'With no argument, EVERY .dcp found in the default '
                         'folders is converted — scope that with '
                         '$CAMICC_DCP_DIR (e.g. dcps/Camera/<your camera>) '
                         'or the full ~4,400-profile tree will be converted')
    ap.add_argument('-o', '--outdir', default='icc',
                    help='output directory for the .icc files '
                         '(default: ./icc)')
    ap.add_argument('--variant', choices=sorted(VARIANT_SETS), default='all',
                    help='"look" bakes in the DCP tone curve (use with the tone '
                         'mapper disabled in darktable); "colors" is color-only '
                         'for the scene-referred workflow; "headroom" is colors-'
                         'only with 2.7 EV of highlight headroom baked in — '
                         'immune to the LUT-profile highlight clipping, but '
                         'needs the exposure setup from camicc-styles; '
                         '"both" = look+colors (default: all)')
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
    ap.add_argument('--cct', type=float, default=None, metavar='KELVIN',
                    help='build the profile for this shot color temperature: '
                         'dual-illuminant matrices and HueSatMap tables are '
                         'interpolated there like Lightroom does per image '
                         '(e.g. --cct 3200 for indoor tungsten light; '
                         'overrides --hsm-illuminant). The profile name gets '
                         'a "@<K>K" suffix')
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
                     'folders ($CAMICC_DCP_DIR, ./dcps, '
                     '~/.cache/camicc/dcps) — populate them with '
                     'camicc-fetch-dcps, or pass files explicitly')
        print(f'no DCP given — converting all {len(inputs)} profiles from '
              'the default DCP folders')

    expanded = []
    for arg in inputs:
        for path in resolve_dcps(arg):
            if path not in expanded:
                expanded.append(path)
    if len(expanded) > len(inputs):
        print(f'{len(expanded)} DCPs matched')

    written = []
    for path in expanded:
        try:
            dcp = parse_dcp(path)
        except ValueError as e:
            if not bulk:
                raise
            print(f'skipping {path}: {e}', file=sys.stderr)
            continue
        base = a.name or ' '.join(x for x in (dcp.unique_camera_model, dcp.profile_name) if x)
        base = base or os.path.splitext(os.path.basename(path))[0]
        cct = a.cct
        if cct and not illuminant_dependent(dcp):
            print(f'{os.path.basename(path)}: this profile renders '
                  'identically under any illuminant (single/equal forward '
                  'matrices, no dual HueSatMap) — ignoring --cct',
                  file=sys.stderr)
            cct = None
        if cct:
            base += f' @{round(cct)}K'

        want = VARIANT_SETS[a.variant]
        variants = []
        if 'look' in want:
            if dcp.tone_curve is not None or curve_data is not None:
                variants.append(('look', f'{base} (camera look)'))
            elif a.variant == 'look':
                print(f'{path}: no tone curve in DCP and no --custom-curve; '
                      f'skipping look variant', file=sys.stderr)
        if 'colors' in want:
            variants.append(('colors', f'{base} (colors only)'))
        if 'headroom' in want:
            variants.append(('headroom', f'{base} (colors only, headroom)'))

        for kind, name in variants:
            grid = a.grid
            try:
                if kind == 'look':
                    lab, itab = render_clut(
                        dcp, grid=grid, hsm_illuminant=a.hsm_illuminant,
                        curve='custom' if curve_data else 'dcp',
                        curve_mode=a.curve_mode, curve_data=curve_data,
                        cct=cct)
                elif kind == 'headroom':
                    grid = max(a.grid, HEADROOM_GRID)
                    lab, itab = render_clut(
                        dcp, grid=grid, hsm_illuminant=a.hsm_illuminant,
                        curve='none', pre_ev=0.0, cct=cct,
                        headroom=HEADROOM_EV)
                else:
                    lab, itab = render_clut(
                        dcp, grid=grid, hsm_illuminant=a.hsm_illuminant,
                        curve='none', pre_ev=0.0, cct=cct)
            except ValueError as e:
                if not bulk:
                    raise
                print(f'skipping {name}: {e}', file=sys.stderr)
                continue
            out = os.path.join(a.outdir, f'{name}.icc')
            write_icc(out, name, lab, itab, grid,
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
