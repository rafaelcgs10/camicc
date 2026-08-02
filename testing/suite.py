#!/usr/bin/env python3
"""Folder-based comparison suite.

Point this at a folder named after your camera that contains raw+JPEG pairs
(shoot RAW+JPEG; same filename stem) and a .dcp profile for the camera:

    Canon EOS RP/
      Canon EOS RP Camera Standard.dcp
      IMG_0001.CR3   IMG_0001.JPG
      IMG_0002.CR3   IMG_0002.JPG
      ...

Every pair is run through the full comparison (see compare.py) and a
report.md is written with the metrics table and side-by-side montage of
every image, plus an aggregate table averaged over all images.

Example:
    python3 testing/suite.py "Canon EOS RP" -o results/

The .dcp may also be passed explicitly with --dcp (useful when the folder
holds several).
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import compare_one                        # noqa: E402
import dtxmp                                           # noqa: E402

RAW_EXTS = {'.cr3', '.cr2', '.crw', '.nef', '.nrw', '.arw', '.raf', '.orf',
            '.rw2', '.dng', '.pef', '.srw', '.iiq', '.3fr', '.fff', '.x3f'}
JPEG_EXTS = ('.jpg', '.jpeg', '.JPG', '.JPEG')

# Additional sources of truth: a file named <prefix>_<rawstem>.jpg next to
# the raw is treated as another reference rendering to compare against
# (besides the camera JPEG). Known prefixes get a pretty name; any other
# prefix works too and is title-cased.
REFERENCE_PREFIXES = {
    'lightroom': 'Lightroom',
    'capture_one': 'Capture One',
    'captureone': 'Capture One',
    'dxo': 'DxO PhotoLab',
    'luminar': 'Luminar',
    'on1': 'ON1',
}


def find_refs(folder: Path, raw: Path, jpeg: Path):
    """The reference list for one raw: the camera JPEG first, then every
    <prefix>_<stem>.jpg file as an additional source of truth."""
    refs = [('camera', 'Camera JPEG', jpeg)]
    suffix = '_' + raw.stem
    for f in sorted(folder.iterdir()):
        if f.suffix not in JPEG_EXTS or not f.stem.endswith(suffix):
            continue
        prefix = f.stem[:-len(suffix)]
        if not prefix:
            continue
        label = REFERENCE_PREFIXES.get(prefix.lower(),
                                       prefix.replace('_', ' ').title())
        refs.append((prefix.lower(), label, f))
    return refs


def check_license(folder: Path):
    """Camera folders are meant to be committed with their photos, so a
    license file for the photographs is mandatory."""
    if not any(folder.glob('LICENSE*')):
        sys.exit(
            f'{folder}: no LICENSE file found.\n'
            'Camera test folders are committed to the repository including '
            'the photos, so they must carry a license for them, e.g.:\n\n'
            '  This file is licensed Creative Commons, By-Attribution, '
            'Share-Alike.\n'
            '  (https://creativecommons.org/licenses/by-sa/4.0/)\n\n'
            f'Write that (plus your attribution) into {folder}/LICENSE '
            'and re-run.')


def find_pairs(folder: Path):
    pairs = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in RAW_EXTS:
            continue
        jpeg = next((f.with_suffix(e) for e in JPEG_EXTS
                     if f.with_suffix(e).exists()), None)
        if jpeg is None:
            print(f'note: {f.name} has no matching JPEG, skipped',
                  file=sys.stderr)
            continue
        pairs.append((f, jpeg))
    return pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('folder', help='folder (named after the camera) with '
                                   'raw+JPEG pairs and a .dcp profile')
    ap.add_argument('--dcp', default=None,
                    help='DCP profile (default: the single .dcp in the folder)')
    ap.add_argument('--tonemapper', choices=sorted(dtxmp.TONEMAPPERS),
                    default=os.environ.get('DCP2ICC_TONEMAPPER', 'sigmoid'),
                    help='darktable tone mapper for the "colors only" and '
                         'default renders (default: sigmoid)')
    ap.add_argument('--keep', action='store_true',
                    help='keep the intermediate renders/XMPs/dtconfig '
                         '(deleted by default, only the report files remain)')
    ap.add_argument('-o', '--outdir', default=None,
                    help='output directory (default: <folder>/comparisons)')
    a = ap.parse_args()

    folder = Path(a.folder)
    if not folder.is_dir():
        sys.exit(f'{folder}: not a directory')
    check_license(folder)
    camera = folder.resolve().name

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
    out = Path(a.outdir) if a.outdir else folder / 'comparisons'
    out.mkdir(parents=True, exist_ok=True)
    print(f'{camera}: {len(pairs)} pair(s), DCP "{dcp.name}"\n')

    per_image = []                       # (stem, refs, {ref_label: rows})
    # ref_label -> render label -> [(mean, p95), ...]
    totals = collections.defaultdict(lambda: collections.defaultdict(list))
    ref_order = []                       # ref labels in first-seen order
    for raw, jpeg in pairs:
        stem = raw.stem
        print(f'=== {stem} ===')
        refs = find_refs(folder, raw, jpeg)
        all_rows = compare_one(raw, refs, dcp, out / stem,
                               tonemapper=a.tonemapper, cleanup=not a.keep)
        per_image.append((stem, refs, all_rows))
        for ref_label, rows in all_rows.items():
            if ref_label not in ref_order:
                ref_order.append(ref_label)
            for label, m, p95 in rows:
                totals[ref_label][label].append((m, p95))
        print()

    # report.md: aggregate table(s) + per-image sections with montages
    lines = [f'# {camera} — dcp2icc comparison suite', '',
             f'DCP: `{dcp.name}` — {len(pairs)} image(s), tone mapper: '
             f'{a.tonemapper}. Mean absolute pixel difference on the '
             'central 80% of the frame (0–255, lower is better), against '
             'each available source of truth.', '']
    if len(per_image) > 1:
        for ref_label in ref_order:
            lines += [f'## Aggregate vs {ref_label}', '',
                      '| Rendering | mean diff | p95 | images |',
                      '|---|---|---|---|']
            agg = sorted(((sum(m for m, _ in v) / len(v),
                           sum(p for _, p in v) / len(v), k, len(v))
                          for k, v in totals[ref_label].items()))
            lines += [f'| {k} | {m:.1f} | {p:.0f} | {n} |'
                      for m, p, k, n in agg]
            lines.append('')
    for stem, refs, all_rows in per_image:
        lines += [f'## {stem}', '']
        for i, (slug, ref_label, _) in enumerate(refs):
            img = ('comparison-full.jpg' if i == 0
                   else f'comparison-{slug}.jpg')
            if len(refs) > 1:
                lines += [f'### vs {ref_label}', '']
            lines += ['| Rendering | mean diff | p95 |', '|---|---|---|']
            lines += [f'| {n} | {m:.1f} | {p:.0f} |'
                      for n, m, p in all_rows[ref_label]]
            lines += ['', f'![{stem} vs {ref_label}]({stem}/{img})', '']
    (out / 'report.md').write_text('\n'.join(lines))
    print(f'report: {out / "report.md"}')


if __name__ == '__main__':
    main()
