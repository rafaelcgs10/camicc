#!/usr/bin/env python3
"""Sigmoid parameter search for the "colors only" profile.

Grid-searches the sigmoid tone mapper's contrast and skew over every
raw+JPEG pair in a camera folder (same layout as suite.py: pairs + a .dcp),
always scoring darktable's built-in sigmoid presets as well, and writes a
sweep-report.md ranking every configuration by its average distance to the
out-of-camera JPEGs.

The grid is configured per parameter as start + step + number of steps:

    python3 testing/sweep.py Canon\\ EOS\\ RP \\
        --contrast-start 1.5 --contrast-step 0.15 --contrast-steps 5 \\
        --skew-start 0 --skew-step 0.15 --skew-steps 4

The defaults above give 20 combinations; with the 5 presets that is 25
darktable renders per image.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import (METRIC, build_profiles, labeled, load_rgb,      # noqa: E402
                     load_rgb_fit, metrics, montage, run_darktable)
from suite import check_license, find_pairs                          # noqa: E402
import dtxmp                                                         # noqa: E402

import numpy as np                                                   # noqa: E402


def grid(start, step, steps):
    return [round(start + i * step, 4) for i in range(steps)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('folder', help='folder (named after the camera) with '
                                   'raw+JPEG pairs and a .dcp profile')
    ap.add_argument('--dcp', default=None,
                    help='DCP profile (default: the single .dcp in the folder)')
    ap.add_argument('--contrast-start', type=float, default=1.5,
                    help='first contrast value (default: 1.5, the sigmoid default)')
    ap.add_argument('--contrast-step', type=float, default=0.15,
                    help='contrast increment per step (default: 0.15)')
    ap.add_argument('--contrast-steps', type=int, default=5,
                    help='number of contrast values (default: 5)')
    ap.add_argument('--skew-start', type=float, default=0.0,
                    help='first skew value (default: 0.0, the sigmoid default)')
    ap.add_argument('--skew-step', type=float, default=0.15,
                    help='skew increment per step (default: 0.15)')
    ap.add_argument('--skew-steps', type=int, default=4,
                    help='number of skew values (default: 4)')
    ap.add_argument('--no-presets', action='store_true',
                    help='skip the built-in sigmoid presets')
    ap.add_argument('--keep', action='store_true',
                    help='keep the rendered PNGs/XMPs/dtconfig (deleted by '
                         'default, only sweep-report.md remains)')
    ap.add_argument('-o', '--outdir', default=None,
                    help='output directory (default: <folder>/sweep)')
    a = ap.parse_args()

    folder = Path(a.folder)
    if not folder.is_dir():
        sys.exit(f'{folder}: not a directory')
    check_license(folder)
    dcp = Path(a.dcp) if a.dcp else None
    if dcp is None:
        dcps = sorted(folder.glob('*.dcp')) + sorted(folder.glob('*.DCP'))
        if len(dcps) != 1:
            sys.exit(f'{folder}: found {len(dcps)} .dcp files; '
                     f'pass the one to use with --dcp')
        dcp = dcps[0]
    pairs = find_pairs(folder)
    if not pairs:
        sys.exit(f'{folder}: no raw+JPEG pairs found')

    out = Path(a.outdir) if a.outdir else folder / 'sweep'
    out.mkdir(parents=True, exist_ok=True)
    cfg = out / 'dtconfig'
    icc = build_profiles(dcp, cfg / 'color' / 'in')['colors only']

    candidates = []      # (label, params-blob)
    if not a.no_presets:
        candidates += [(f'preset: {name}', dtxmp.sigmoid_params(**kw))
                       for name, kw in dtxmp.SIGMOID_PRESETS.items()]
    for c in grid(a.contrast_start, a.contrast_step, a.contrast_steps):
        for s in grid(a.skew_start, a.skew_step, a.skew_steps):
            candidates.append((f'contrast {c}, skew {s}',
                               dtxmp.sigmoid_params(contrast=c, skew=s)))
    print(f'{folder.resolve().name}: {len(pairs)} image(s) x '
          f'{len(candidates)} configuration(s) = '
          f'{len(pairs) * len(candidates)} renders\n')

    results = {}         # label -> {stem: mean}
    for raw, jpeg in pairs:
        ref = load_rgb(jpeg, METRIC)
        for label, blob in candidates:
            stem = (raw.stem + '_' + label).translate(
                str.maketrans(' .,:', '____'))
            xmp, png = out / f'{stem}.xmp', out / f'{stem}.png'
            png.unlink(missing_ok=True)
            dtxmp.make_xmp(raw.name, str(xmp), str(icc), True, 0.7,
                           tonemapper_op='sigmoid',
                           tonemapper_params=(dtxmp.SIGMOID_VERSION, blob))
            run_darktable(raw, xmp, png, cfg)
            m = float(metrics(load_rgb(png, METRIC), ref)[0])
            results.setdefault(label, {})[raw.stem] = m
            print(f'{raw.stem}  {label}: {m:.2f}', flush=True)
            if not a.keep:      # renders are large; drop them immediately
                png.unlink(missing_ok=True)
                xmp.unlink(missing_ok=True)

    stems = [r.stem for r, _ in pairs]
    ranked = sorted((sum(per.values()) / len(per), label, per)
                    for label, per in results.items())
    best_avg, best_label, best_per = ranked[0]

    # comparison montage: camera JPEG vs the best configuration, every image
    best_blob = dict(candidates)[best_label]
    tiles = []
    for raw, jpeg in pairs:
        xmp, png = out / 'best.xmp', out / 'best.png'
        png.unlink(missing_ok=True)
        dtxmp.make_xmp(raw.name, str(xmp), str(icc), True, 0.7,
                       tonemapper_op='sigmoid',
                       tonemapper_params=(dtxmp.SIGMOID_VERSION, best_blob))
        run_darktable(raw, xmp, png, cfg)
        # letterboxed: images in the folder may mix orientations
        tiles.append(labeled(load_rgb_fit(jpeg),
                             f'{raw.stem} - Camera JPEG'))
        tiles.append(labeled(load_rgb_fit(png),
                             f'{best_label} - {best_per[raw.stem]:.1f}'))
        if not a.keep:
            png.unlink(missing_ok=True)
            xmp.unlink(missing_ok=True)
    montage(tiles, cols=2).save(out / 'comparison-best.jpg', quality=88)

    lines = [f'# Sigmoid parameter search — {folder.resolve().name}', '',
             f'DCP: `{dcp.name}`, colors-only profile, exposure +0.7 EV. '
             'Mean absolute pixel difference vs the out-of-camera JPEG '
             '(0–255, lower is better), per image and averaged.', '',
             '| sigmoid setting | ' + ' | '.join(stems) + ' | avg |',
             '|---|' + '---|' * (len(stems) + 1)]
    for avg, label, per in ranked:
        cells = ' | '.join(f'{per[s]:.1f}' if s in per else '—'
                           for s in stems)
        lines.append(f'| {label} | {cells} | **{avg:.1f}** |')
    lines += ['', f'Best: **{best_label}** (avg {best_avg:.1f}). '
              'Camera JPEG vs the best configuration:', '',
              '![best vs JPEG](comparison-best.jpg)']
    (out / 'sweep-report.md').write_text('\n'.join(lines) + '\n')
    if not a.keep:
        import shutil
        shutil.rmtree(cfg, ignore_errors=True)

    print('\n' + '\n'.join(lines[4:]))
    print(f'report: {out / "sweep-report.md"}')


if __name__ == '__main__':
    main()
