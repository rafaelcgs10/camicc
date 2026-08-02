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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import (METRIC, build_profiles, labeled, load_rgb,      # noqa: E402
                     load_rgb_fit, match_dcp, metrics, montage,
                     run_darktable)
from suite import check_license, find_pairs, find_refs               # noqa: E402
import dtxmp                                                         # noqa: E402



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
        if len(dcps) > 1:
            sys.exit(f'{folder}: found {len(dcps)} .dcp files; '
                     f'pass the one to use with --dcp')
        if dcps:
            dcp = dcps[0]
        # else: auto-match per image from the default DCP folders
    pairs = find_pairs(folder)
    if not pairs:
        sys.exit(f'{folder}: no raw+JPEG pairs found')

    out = Path(a.outdir) if a.outdir else folder / 'sweep'
    out.mkdir(parents=True, exist_ok=True)
    lock = out / '.run.lock'
    try:
        lock.touch(exist_ok=False)
    except FileExistsError:
        sys.exit(f'{out}: another sweep seems to be running in this '
                 f'directory. If no other run is active, delete {lock} '
                 f'and retry.')
    cfg = out / 'dtconfig'
    icc_cache = {}

    def icc_for(dcp_path):
        dcp_path = Path(dcp_path)
        if dcp_path not in icc_cache:
            icc_cache[dcp_path] = build_profiles(
                dcp_path, cfg / 'color' / 'in')['colors only']
        return icc_cache[dcp_path]

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

    # results[ref_label][config_label][stem] = mean; every render is scored
    # against every source of truth available for its image
    results = {}
    ref_slugs = {}                       # ref_label -> slug
    ref_order = []                       # ref labels in first-seen order
    img_refs = []                        # (raw, [(slug, label, path), ...])
    for raw, jpeg in pairs:
        pair_dcp = dcp or match_dcp(jpeg)
        if pair_dcp is None:
            print(f'note: no DCP matches {jpeg.name} in the default DCP '
                  'folders (run dcp2icc-fetch-dcps); pair skipped',
                  file=sys.stderr)
            continue
        if dcp is None:
            print(f'{raw.stem}: auto-matched DCP {Path(pair_dcp).name}')
        icc = icc_for(pair_dcp)
        refs = find_refs(folder, raw, jpeg)
        img_refs.append((raw, refs, icc))
        loaded = [(label, load_rgb(p, METRIC)) for _, label, p in refs]
        for slug, label, _ in refs:
            ref_slugs[label] = slug
            if label not in ref_order:
                ref_order.append(label)
        for label, blob in candidates:
            stem = (raw.stem + '_' + label).translate(
                str.maketrans(' .,:', '____'))
            xmp, png = out / f'{stem}.xmp', out / f'{stem}.png'
            png.unlink(missing_ok=True)
            dtxmp.make_xmp(raw.name, str(xmp), str(icc), True, 0.7,
                           tonemapper_op='sigmoid',
                           tonemapper_params=(dtxmp.SIGMOID_VERSION, blob))
            run_darktable(raw, xmp, png, cfg)
            img = load_rgb(png, METRIC)
            ms = []
            for ref_label, ref_img in loaded:
                m = float(metrics(img, ref_img)[0])
                results.setdefault(ref_label, {}) \
                       .setdefault(label, {})[raw.stem] = m
                ms.append(f'{m:.2f}')
            print(f'{raw.stem}  {label}: ' + ' / '.join(ms), flush=True)
            if not a.keep:      # renders are large; drop them immediately
                png.unlink(missing_ok=True)
                xmp.unlink(missing_ok=True)

    dcp_desc = (f'`{dcp.name}`' if dcp
                else 'auto-matched per image from the camera model and '
                     'Picture Style')
    lines = [f'# Sigmoid parameter search — {folder.resolve().name}', '',
             f'DCP: {dcp_desc}, colors-only profile, exposure +0.7 EV. '
             'Mean absolute pixel difference on the central 80% of the '
             'frame (0–255, lower is better), per image and averaged, '
             'against each available source of truth.']
    for ref_label in ref_order:
        per_config = results[ref_label]
        stems = sorted({s for per in per_config.values() for s in per})
        ranked = sorted((sum(per.values()) / len(per), label, per)
                        for label, per in per_config.items())
        best_avg, best_label, best_per = ranked[0]

        # montage: this source of truth vs its best configuration. Match by
        # full label: the 'camera' slug is shared by different Picture
        # Styles, which are separate reference groups.
        best_blob = dict(candidates)[best_label]
        tiles = []
        for raw, refs, img_icc in img_refs:
            ref_path = next((p for _, l, p in refs if l == ref_label), None)
            if ref_path is None:
                continue
            xmp, png = out / 'best.xmp', out / 'best.png'
            png.unlink(missing_ok=True)
            dtxmp.make_xmp(raw.name, str(xmp), str(img_icc), True, 0.7,
                           tonemapper_op='sigmoid',
                           tonemapper_params=(dtxmp.SIGMOID_VERSION,
                                              best_blob))
            run_darktable(raw, xmp, png, cfg)
            # letterboxed: images in the folder may mix orientations
            tiles.append(labeled(load_rgb_fit(ref_path),
                                 f'{raw.stem} - {ref_label}'))
            tiles.append(labeled(load_rgb_fit(png),
                                 f'{best_label} - {best_per[raw.stem]:.1f}'))
            if not a.keep:
                png.unlink(missing_ok=True)
                xmp.unlink(missing_ok=True)
        fname = re.sub(r'[^a-z0-9]+', '-', ref_label.lower()).strip('-')
        montage_name = ('comparison-best.jpg' if ref_label == ref_order[0]
                        else f'comparison-best-{fname}.jpg')
        montage(tiles, cols=2).save(out / montage_name, quality=88)

        lines += ['', f'## vs {ref_label}', '',
                  '| sigmoid setting | ' + ' | '.join(stems) + ' | avg |',
                  '|---|' + '---|' * (len(stems) + 1)]
        for avg, label, per in ranked:
            cells = ' | '.join(f'{per[s]:.1f}' if s in per else '—'
                               for s in stems)
            lines.append(f'| {label} | {cells} | **{avg:.1f}** |')
        lines += ['', f'Best: **{best_label}** (avg {best_avg:.1f}). '
                  f'{ref_label} vs the best configuration:', '',
                  f'![best vs {ref_label}]({montage_name})']
    (out / 'sweep-report.md').write_text('\n'.join(lines) + '\n')
    if not a.keep:
        import shutil
        shutil.rmtree(cfg, ignore_errors=True)
    lock.unlink(missing_ok=True)

    print('\n' + '\n'.join(lines[3:]))
    print(f'report: {out / "sweep-report.md"}')


if __name__ == '__main__':
    main()
