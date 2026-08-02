#!/usr/bin/env python3
"""Comparative test: how close does a dcp2icc profile get to the camera JPEG?

Takes a raw file, its out-of-camera JPEG and a DCP, then:

1. converts the DCP with dcp2icc (both variants),
2. renders the raw through darktable-cli three ways
   (profile "camera look" / profile "colors only" + tone mapper /
    darktable default matrix + tone mapper),
3. scores every render against the camera JPEG (mean absolute pixel
   difference, p95, on a downscaled common frame),
4. writes a labeled side-by-side montage, optional detail-crop strip and a
   metrics table.

Extra pre-rendered references (e.g. ART or RawTherapee output for the same
raw) can be added with --extra NAME=PATH.

Example:
    python3 testing/compare.py \
        --raw IMG_9399.CR3 --jpeg IMG_9399.JPG \
        --dcp "Canon EOS RP Camera Standard.dcp" \
        --extra ART=art_ref.tif --crop 430,120,830,520 -o results/
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcp2icc.dcp import parse_dcp                      # noqa: E402
from dcp2icc.pipeline import render_clut               # noqa: E402
from dcp2icc.icc import write_icc                      # noqa: E402
import dtxmp                                           # noqa: E402

Image.MAX_IMAGE_PIXELS = None
FRAME = (1200, 800)      # normalized frame for montages/crops
METRIC = (480, 320)      # frame for metrics


def run_darktable(raw, xmp, out, configdir):
    # display-referred workflow => temperature module defaults to "as shot"
    # white balance (DCP-derived profiles expect fully white-balanced input);
    # the auto_presets_applied flag in the XMP blocks the other presets.
    cmd = ['darktable-cli', str(raw), str(xmp), str(out),
           '--core', '--configdir', str(configdir), '--library', ':memory:',
           '--conf', 'write_sidecar_files=never',
           '--conf', 'plugins/darkroom/workflow=display-referred (legacy)',
           '--conf', 'plugins/darkroom/chromatic-adaptation=legacy']
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not Path(out).exists():
        sys.exit(f'darktable-cli failed for {xmp}:\n{r.stdout}\n{r.stderr}\n'
                 f'(is the darktable GUI open without --configdir isolation?)')


def load_rgb(path, size):
    im = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    return im.resize(size, Image.LANCZOS)


def metrics(img, ref):
    a = np.asarray(img).astype(float)
    b = np.asarray(ref).astype(float)
    d = np.abs(a - b)
    return d.mean(), np.percentile(d, 95)


def labeled(im, text, band=34, fontsize=22):
    try:
        font = ImageFont.load_default(size=fontsize)
    except TypeError:
        font = ImageFont.load_default()
    w, h = im.size
    out = Image.new('RGB', (w, h + band), (16, 16, 16))
    out.paste(im, (0, band))
    ImageDraw.Draw(out).text((10, 6), text, fill=(240, 240, 240), font=font)
    return out


def montage(tiles, cols, pad=8, bg=(30, 30, 30)):
    w, h = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    out = Image.new('RGB', (w * cols + pad * (cols + 1),
                            h * rows + pad * (rows + 1)), bg)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        out.paste(t, (pad + c * (w + pad), pad + r * (h + pad)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--raw', required=True, help='raw file (.CR3/.NEF/...)')
    ap.add_argument('--jpeg', required=True, help='out-of-camera JPEG (ground truth)')
    ap.add_argument('--dcp', required=True, help='DCP profile to test')
    ap.add_argument('--extra', action='append', default=[], metavar='NAME=PATH',
                    help='additional pre-rendered reference image(s), e.g. '
                         'ART=art_ref.tif (repeatable)')
    ap.add_argument('--crop', default=None, metavar='X0,Y0,X1,Y1',
                    help=f'detail crop box in the normalized {FRAME[0]}x{FRAME[1]} '
                         'frame for the close-up strip')
    ap.add_argument('-o', '--outdir', default='compare-results')
    a = ap.parse_args()

    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
    cfg = out / 'dtconfig'; profdir = cfg / 'color' / 'in'
    profdir.mkdir(parents=True, exist_ok=True)

    # 1. build both profile variants straight into the test config dir
    dcp = parse_dcp(a.dcp)
    base = ' '.join(x for x in (dcp.unique_camera_model, dcp.profile_name) if x)
    variants = {}
    if dcp.tone_curve is not None:
        lab, itab = render_clut(dcp, curve='dcp', curve_mode='channel')
        p = profdir / f'{base} (camera look).icc'
        write_icc(str(p), f'{base} (camera look)', lab, itab, 33)
        variants['camera look'] = p
    lab, itab = render_clut(dcp, curve='none', pre_ev=0.0)
    p = profdir / f'{base} (colors only).icc'
    write_icc(str(p), f'{base} (colors only)', lab, itab, 33)
    variants['colors only'] = p
    print(f'built {len(variants)} profile(s) from {os.path.basename(a.dcp)}')

    # 2. render
    renders = []   # (label, path)
    jobs = []
    if 'camera look' in variants:
        jobs.append((f'dcp2icc (camera look)', variants['camera look'], False, 0.0))
    jobs.append((f'dcp2icc (colors only)+{dtxmp.OP_TONEMAPPER}',
                 variants['colors only'], True, 0.7))
    jobs.append((f'darktable default ({dtxmp.OP_TONEMAPPER})', None, True, 0.7))
    for label, icc, tm, ev in jobs:
        stem = label.replace(' ', '_').replace('(', '').replace(')', '').replace('+', '_')
        xmp = out / f'{stem}.xmp'; png = out / f'{stem}.png'
        png.unlink(missing_ok=True)
        dtxmp.make_xmp(os.path.basename(a.raw), str(xmp),
                       str(icc) if icc else None, tm, ev)
        run_darktable(a.raw, xmp, png, cfg)
        renders.append((label, png))
        print(f'rendered: {label}')

    # 3. score everything against the camera JPEG
    panels = [('Camera JPEG', a.jpeg)] + renders
    for item in a.extra:
        name, _, path = item.partition('=')
        panels.append((name, path))
    ref = load_rgb(a.jpeg, METRIC)
    rows = []
    for name, path in panels[1:]:
        m, p95 = metrics(load_rgb(path, METRIC), ref)
        rows.append((name, m, p95))
    rows.sort(key=lambda r: r[1])
    table = ['| Rendering | mean diff vs JPEG | p95 |', '|---|---|---|']
    table += [f'| {n} | {m:.1f} | {p:.0f} |' for n, m, p in rows]
    report = '\n'.join(table)
    print('\n' + report)
    (out / 'metrics.md').write_text(report + '\n')

    # 4. montages
    tiles = [labeled(load_rgb(p, (560, 373)), t) for t, p in panels]
    montage(tiles, cols=3).save(out / 'comparison-full.jpg', quality=88)
    if a.crop:
        x0, y0, x1, y1 = (int(v) for v in a.crop.split(','))
        crops = [labeled(load_rgb(p, FRAME).crop((x0, y0, x1, y1))
                         .resize((320, 320), Image.LANCZOS), t, band=30, fontsize=20)
                 for t, p in panels]
        montage(crops, cols=len(crops)).save(out / 'comparison-crop.jpg', quality=90)
    shutil.rmtree(cfg / 'cache', ignore_errors=True)
    print(f'\nresults in {out}/ (metrics.md, comparison-full.jpg'
          + (', comparison-crop.jpg)' if a.crop else ')'))


if __name__ == '__main__':
    main()
