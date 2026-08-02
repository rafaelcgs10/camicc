#!/usr/bin/env python3
"""Comparative test: how close does a dcp2icc profile get to the camera JPEG?

Takes a raw file, its out-of-camera JPEG and a DCP, then:

1. converts the DCP with dcp2icc (both variants),
2. renders the raw through darktable-cli three ways
   (profile "camera look" / profile "colors only" + tone mapper /
    darktable default matrix + tone mapper),
3. renders a RawTherapee reference (native DCP handling) if
   rawtherapee-cli is available,
4. scores every render against the camera JPEG (mean absolute pixel
   difference, p95, on a downscaled common frame),
5. writes a labeled side-by-side montage and a metrics table.

Extra pre-rendered references (e.g. a Lightroom export of the same raw)
can be added with --extra NAME=PATH.

Example:
    python3 testing/compare.py \
        --raw IMG_9399.CR3 --jpeg IMG_9399.JPG \
        --dcp "Canon EOS RP Camera Standard.dcp" -o results/
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
METRIC = (480, 320)      # frame for metrics


def render_rawtherapee(raw, outdir):
    """Render the raw with RawTherapee's default processing (its bundled DCP
    + auto-matched tone curve) as the native-DCP reference. Returns the
    output path, or None if rawtherapee-cli is not available."""
    exe = shutil.which('rawtherapee-cli')
    if exe is None:
        print('rawtherapee-cli not found; skipping the RawTherapee reference',
              file=sys.stderr)
        return None
    out = Path(outdir) / 'rawtherapee_ref.tif'
    out.unlink(missing_ok=True)
    r = subprocess.run([exe, '-o', str(out), '-t', '-b8', '-Y', '-d',
                        '-c', str(raw)], capture_output=True, text=True)
    if not out.exists():
        print(f'rawtherapee-cli failed, skipping the RawTherapee reference:\n'
              f'{r.stdout}\n{r.stderr}', file=sys.stderr)
        return None
    return out


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


def build_profiles(dcp_path, profdir):
    """Convert the DCP into the two ICC variants inside profdir.
    Returns {'camera look': path, 'colors only': path} ('camera look' only
    when the DCP carries a usable tone curve)."""
    profdir = Path(profdir)
    profdir.mkdir(parents=True, exist_ok=True)
    dcp = parse_dcp(str(dcp_path))
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
    return variants


def compare_one(raw, jpeg, dcp_path, outdir, tonemapper='sigmoid', extras=(),
                cleanup=True):
    """Run the full comparison for one raw+JPEG pair.

    Renders the dcp2icc variants and the darktable default through
    darktable-cli, adds the RawTherapee reference if available, scores
    everything against the JPEG and writes metrics.md + comparison-full.jpg
    into outdir. Returns the sorted rows [(label, mean, p95), ...].
    cleanup=True (the default) deletes the intermediate renders, XMPs and
    the darktable config dir afterwards, keeping only the report files."""
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    cfg = out / 'dtconfig'
    variants = build_profiles(dcp_path, cfg / 'color' / 'in')
    print(f'built {len(variants)} profile(s) from {os.path.basename(str(dcp_path))}')

    renders = []   # (label, path)
    jobs = []
    if 'camera look' in variants:
        jobs.append(('dcp2icc (camera look)', variants['camera look'], False, 0.0))
    jobs.append((f'dcp2icc (colors only)+{tonemapper}',
                 variants['colors only'], True, 0.7))
    jobs.append((f'darktable default ({tonemapper})', None, True, 0.7))
    for label, icc, tm, ev in jobs:
        stem = label.replace(' ', '_').replace('(', '').replace(')', '').replace('+', '_')
        xmp = out / f'{stem}.xmp'; png = out / f'{stem}.png'
        png.unlink(missing_ok=True)
        dtxmp.make_xmp(os.path.basename(str(raw)), str(xmp),
                       str(icc) if icc else None, tm, ev,
                       tonemapper_op=tonemapper)
        run_darktable(raw, xmp, png, cfg)
        renders.append((label, png))
        print(f'rendered: {label}')

    rt_ref = render_rawtherapee(raw, out)
    if rt_ref is not None:
        renders.append(('RawTherapee (native DCP)', rt_ref))
        print('rendered: RawTherapee (native DCP)')

    candidates = list(renders)
    for item in extras:
        name, _, path = item.partition('=')
        candidates.append((name, path))
    ref = load_rgb(jpeg, METRIC)
    scored = []
    for name, path in candidates:
        m, p95 = metrics(load_rgb(path, METRIC), ref)
        scored.append((name, path, m, p95))
    scored.sort(key=lambda r: r[2])
    rows = [(n, m, p) for n, _, m, p in scored]
    table = ['| Rendering | mean diff vs JPEG | p95 |', '|---|---|---|']
    table += [f'| {n} | {m:.1f} | {p:.0f} |' for n, m, p in rows]
    report = '\n'.join(table)
    print('\n' + report)
    (out / 'metrics.md').write_text(report + '\n')

    # montage sorted by similarity: camera JPEG first, then best match first
    tiles = [labeled(load_rgb(jpeg, (560, 373)), 'Camera JPEG')]
    tiles += [labeled(load_rgb(p, (560, 373)), f'{n} - {m:.1f}')
              for n, p, m, _ in scored]
    montage(tiles, cols=3).save(out / 'comparison-full.jpg', quality=88)
    if cleanup:
        for _, path in renders:
            Path(path).unlink(missing_ok=True)
            Path(path).with_suffix('.xmp').unlink(missing_ok=True)
        shutil.rmtree(cfg, ignore_errors=True)
    else:
        shutil.rmtree(cfg / 'cache', ignore_errors=True)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--raw', required=True, help='raw file (.CR3/.NEF/...)')
    ap.add_argument('--jpeg', required=True, help='out-of-camera JPEG (ground truth)')
    ap.add_argument('--dcp', required=True, help='DCP profile to test')
    ap.add_argument('--extra', action='append', default=[], metavar='NAME=PATH',
                    help='additional reference image(s) you rendered yourself '
                         'from the same raw in another program, e.g. '
                         'Lightroom=lr.tif (repeatable)')
    ap.add_argument('--tonemapper', choices=sorted(dtxmp.TONEMAPPERS),
                    default=os.environ.get('DCP2ICC_TONEMAPPER', 'sigmoid'),
                    help='darktable tone mapper module for the "colors only" '
                         'and default renders: sigmoid (upstream darktable) '
                         'or agx (spektrafilm fork); also settable via '
                         '$DCP2ICC_TONEMAPPER (default: sigmoid)')
    ap.add_argument('--keep', action='store_true',
                    help='keep the intermediate renders/XMPs/dtconfig '
                         '(deleted by default, only the report files remain)')
    ap.add_argument('-o', '--outdir', default='compare-results')
    a = ap.parse_args()

    compare_one(a.raw, a.jpeg, a.dcp, a.outdir,
                tonemapper=a.tonemapper, extras=a.extra, cleanup=not a.keep)
    print(f'\nresults in {a.outdir}/ (metrics.md, comparison-full.jpg)')


if __name__ == '__main__':
    main()
