#!/usr/bin/env python3
"""Sigmoid parameter search for the "colors only" profile.

Searches the sigmoid tone mapper's contrast and skew over every raw+JPEG
pair in a camera folder (same layout as suite.py) and writes a
sweep-report.md ranking every evaluated configuration per source of truth
(--presets additionally scores darktable's five built-in sigmoid presets).

The default strategy is an adaptive pattern search (the 2D analog of a
binary search): starting from contrast-start/skew-start it evaluates the
four axis neighbors, moves to an improving one, and halves the step when
none improves, until --min-step or --patience rounds without --tol
improvement. Renders are cached, so searching several reference groups
mostly reuses the same renders.

--search grid runs the old exhaustive grid instead, configured per
parameter as start + step + number of steps:

    python3 testing/sweep.py Canon\\ EOS\\ RP --search grid \\
        --contrast-start 1.5 --contrast-step 0.15 --contrast-steps 5 \\
        --skew-start 0 --skew-step 0.15 --skew-steps 4
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import (METRIC, build_profiles, labeled, load_rgb,      # noqa: E402
                     load_rgb_fit, metrics, montage, run_darktable,
                     tile_size)
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
    ap.add_argument('--search', choices=['adaptive', 'grid'],
                    default='adaptive',
                    help='adaptive (default): pattern search that starts at '
                         'contrast-start/skew-start, moves to improving '
                         'neighbors and halves the step when stuck — far '
                         'fewer renders than the exhaustive grid')
    ap.add_argument('--tol', type=float, default=0.1,
                    help='adaptive: minimum score improvement per round to '
                         'count as progress (default: 0.1)')
    ap.add_argument('--patience', type=int, default=2,
                    help='adaptive: stop after this many rounds without at '
                         'least --tol improvement (default: 2)')
    ap.add_argument('--init-step', type=float, default=0.45,
                    help='adaptive: initial step size for both axes '
                         '(default: 0.45 — crosses the useful range fast)')
    ap.add_argument('--min-step', type=float, default=0.15,
                    help='adaptive: stop refining below this step size '
                         '(default: 0.15 — same resolution as the grid; '
                         'finer steps change scores by less than 0.3, '
                         'which is visually meaningless)')
    ap.add_argument('--presets', action='store_true',
                    help='also score darktable\'s five built-in sigmoid '
                         'presets (25 extra renders on a 5-image folder). '
                         'Off by default: their ranking never changes and '
                         'the search already starts from the best one, the '
                         'scene-referred default (contrast 1.5, skew 0)')
    ap.add_argument('--per-image', action='store_true',
                    help='additionally pick the best configuration per '
                         'image (not only per folder average) and write an '
                         'individual truth-vs-best montage for every image')
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

    # collect pairs and their references up front; every reference has its
    # own matched DCP (usually all the same), so ICCs are per (raw, ref)
    results = {}         # results[ref_label][config_label][stem] = mean
    ref_slugs = {}       # ref_label -> slug
    ref_order = []       # ref labels in first-seen order
    img_refs = []        # (raw, [(slug, label, path, icc), ...])
    loaded_refs = {}     # (ref_label, stem) -> PIL image
    for raw, jpeg in pairs:
        refs = find_refs(folder, raw, jpeg, dcp_override=dcp)
        if any(r[3] is None for r in refs):
            print(f'note: no DCP matches {jpeg.name} in the default DCP '
                  'folders (run camicc-fetch-dcps); pair skipped',
                  file=sys.stderr)
            continue
        if dcp is None:
            for _, rlabel, _, rdcp in refs:
                print(f'{raw.stem}: auto-matched DCP for {rlabel}: '
                      f'{Path(rdcp).name}')
        refs = [(slug, label, path, icc_for(rdcp))
                for slug, label, path, rdcp in refs]
        img_refs.append((raw, refs))
        for slug, label, path, _ in refs:
            loaded_refs[(label, raw.stem)] = load_rgb(path, METRIC)
            ref_slugs[label] = slug
            if label not in ref_order:
                ref_order.append(label)

    n_renders = [0]
    blobs = {}           # config label -> params blob (for re-renders)

    def evaluate(label, blob):
        """Render this configuration on every image (cached across the
        search). One render per distinct ICC of the image; the render is
        scored against every source of truth using that ICC."""
        blobs[label] = blob
        for raw, refs in img_refs:
            by_icc = {}          # icc -> [ref labels]
            for _, rlabel, _, icc in refs:
                by_icc.setdefault(icc, []).append(rlabel)
            for j, (icc, rlabels) in enumerate(by_icc.items()):
                if all(raw.stem in results.get(rl, {}).get(label, {})
                       for rl in rlabels):
                    continue
                stem = (raw.stem + (f'_d{j}' if len(by_icc) > 1 else '')
                        + '_' + label).translate(
                    str.maketrans(' .,:', '____'))
                xmp, png = out / f'{stem}.xmp', out / f'{stem}.png'
                png.unlink(missing_ok=True)
                dtxmp.make_xmp(raw.name, str(xmp), str(icc), True, 0.7,
                               tonemapper_op='sigmoid',
                               tonemapper_params=(dtxmp.SIGMOID_VERSION,
                                                  blob))
                run_darktable(raw, xmp, png, cfg)
                n_renders[0] += 1
                img = load_rgb(png, METRIC)
                ms = []
                for rl in rlabels:
                    m = float(metrics(img, loaded_refs[(rl, raw.stem)])[0])
                    results.setdefault(rl, {}) \
                           .setdefault(label, {})[raw.stem] = m
                    ms.append(f'{m:.2f}')
                print(f'{raw.stem}  {label}: ' + ' / '.join(ms), flush=True)
                if not a.keep:  # renders are large; drop them immediately
                    png.unlink(missing_ok=True)
                    xmp.unlink(missing_ok=True)

    def eval_config(c, s):
        """Evaluate sigmoid (contrast c, skew s); returns its label."""
        c, s = round(c, 4), round(s, 4)
        label = f'contrast {c}, skew {s}'
        evaluate(label, dtxmp.sigmoid_params(contrast=c, skew=s))
        return label

    def objective(ref_label, label):
        per = results[ref_label].get(label)
        stems = [r.stem for r, refs, _ in img_refs
                 if any(l == ref_label for _, l, _ in refs)]
        vals = [per[s] for s in stems if per and s in per]
        return sum(vals) / len(vals) if vals else float('inf')

    if a.presets:
        for name, kw in dtxmp.SIGMOID_PRESETS.items():
            evaluate(f'preset: {name}', dtxmp.sigmoid_params(**kw))

    if a.search == 'grid':
        for c in grid(a.contrast_start, a.contrast_step, a.contrast_steps):
            for s in grid(a.skew_start, a.skew_step, a.skew_steps):
                eval_config(c, s)
    else:
        # adaptive pattern search per reference group (the 2D analog of a
        # binary search): evaluate the 4 axis neighbors of the current
        # point, move to an improving one, halve the step when stuck.
        # Renders are cached, so later groups mostly reuse earlier work.
        for ref_label in ref_order:
            cur = (a.contrast_start, a.skew_start)
            best_label = eval_config(*cur)
            best = objective(ref_label, best_label)
            step_c = step_s = a.init_step
            stall = 0
            print(f'-- adaptive search vs {ref_label}: start {best:.2f}')
            while (step_c >= a.min_step or step_s >= a.min_step) \
                    and stall < a.patience:
                round_start = best
                # greedy: move to the FIRST improving neighbor (contrast
                # axis first — it dominates), so an improving round costs
                # 1–4 evaluations instead of always 4
                improved = False
                for dc, ds in ((step_c, 0), (-step_c, 0),
                               (0, step_s), (0, -step_s)):
                    c = min(max(cur[0] + dc, 0.5), 3.0)
                    s = min(max(cur[1] + ds, -1.0), 1.0)
                    lbl = eval_config(c, s)
                    val = objective(ref_label, lbl)
                    if val < best - 1e-9:
                        cur, best, best_label = (c, s), val, lbl
                        improved = True
                        break
                if not improved:
                    step_c, step_s = step_c / 2, step_s / 2
                # early stop: proximity no longer improves by at least tol
                stall = 0 if round_start - best >= a.tol else stall + 1
                print(f'-- vs {ref_label}: best {best:.2f} at {best_label} '
                      f'(step {step_c:.3f}, stall {stall}/{a.patience})')
            print(f'-- vs {ref_label}: done, best {best:.2f} '
                  f'({best_label})')
    print(f'\n{n_renders[0]} darktable renders performed')

    dcp_desc = (f'`{dcp.name}`' if dcp
                else 'auto-matched per image from the camera model and '
                     'Picture Style')
    lines = [f'# Sigmoid parameter search — {folder.resolve().name}', '',
             f'DCP: {dcp_desc}, colors-only profile, exposure +0.7 EV, '
             f'{a.search} search ({n_renders[0]} renders). '
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
        best_blob = blobs[best_label]
        tiles = []
        for raw, refs in img_refs:
            hit = next(((p, icc) for _, l, p, icc in refs
                        if l == ref_label), None)
            if hit is None:
                continue
            ref_path, img_icc = hit
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

        if a.per_image:
            # each image's own optimum for this reference, with an
            # individual truth-vs-best montage per image
            per_image_rows = []          # (stem, label, mean, montage name)
            for raw, refs in img_refs:
                hit = next(((p, icc) for _, l, p, icc in refs
                            if l == ref_label), None)
                if hit is None:
                    continue
                ref_path, img_icc = hit
                stem = raw.stem
                img_best, img_label = min(
                    (per[stem], label) for label, per in per_config.items()
                    if stem in per)
                blob = blobs[img_label]
                xmp, png = out / 'best.xmp', out / 'best.png'
                png.unlink(missing_ok=True)
                dtxmp.make_xmp(raw.name, str(xmp), str(img_icc), True, 0.7,
                               tonemapper_op='sigmoid',
                               tonemapper_params=(dtxmp.SIGMOID_VERSION,
                                                  blob))
                run_darktable(raw, xmp, png, cfg)
                ts = tile_size(ref_path)
                fs = max(13, min(22, ts[0] // 26))
                pair = [labeled(load_rgb(ref_path, ts),
                                f'{stem} - {ref_label}', fontsize=fs),
                        labeled(load_rgb(png, ts),
                                f'{img_label} - {img_best:.1f}',
                                fontsize=fs)]
                img_name = f'comparison-best-{fname}-{stem}.jpg'
                montage(pair, cols=2).save(out / img_name, quality=88)
                if not a.keep:
                    png.unlink(missing_ok=True)
                    xmp.unlink(missing_ok=True)
                per_image_rows.append((stem, img_label, img_best, img_name))
            lines += ['', f'### Per-image best (vs {ref_label})', '',
                      '| image | best sigmoid setting | mean diff |',
                      '|---|---|---|']
            lines += [f'| {s} | {l} | {m:.1f} |'
                      for s, l, m, _ in per_image_rows]
            for s, _, _, img_name in per_image_rows:
                lines += ['', f'![{s} vs {ref_label}]({img_name})']
    (out / 'sweep-report.md').write_text('\n'.join(lines) + '\n')
    if not a.keep:
        import shutil
        shutil.rmtree(cfg, ignore_errors=True)
    lock.unlink(missing_ok=True)

    print('\n' + '\n'.join(lines[3:]))
    print(f'report: {out / "sweep-report.md"}')


if __name__ == '__main__':
    main()
