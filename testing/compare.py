#!/usr/bin/env python3
"""Comparative test: how close does a camicc profile get to the camera JPEG?

Takes a raw file, its out-of-camera JPEG and a DCP, then:

1. converts the DCP with camicc (both variants),
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
import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camicc.dcp import parse_dcp                      # noqa: E402
from camicc.pipeline import (render_clut, estimate_cct,   # noqa: E402
                             illuminant_dependent)
from camicc.icc import write_icc                      # noqa: E402
import dtxmp                                           # noqa: E402

Image.MAX_IMAGE_PIXELS = None
METRIC = (480, 320)      # frame for metrics


def exif_tags(path, *tags):
    """{tag: value} via exiftool; empty when exiftool is unavailable."""
    exe = shutil.which('exiftool')
    if exe is None:
        return {}
    r = subprocess.run([exe, '-S'] + [f'-{t}' for t in tags] + [str(path)],
                       capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        k, sep, v = line.partition(':')
        if sep:
            out[k.strip()] = v.strip()
    return out


def picture_style(jpeg):
    """The camera Picture Style of a JPEG, e.g. 'Standard', 'Auto',
    'User Def. 1' — or None when unavailable."""
    return exif_tags(jpeg, 'PictureStyle').get('PictureStyle') or None


def default_dcp_dirs():
    """Folders searched when auto-matching DCPs: $CAMICC_DCP_DIR (when set
    it replaces the others), else ./dcps (as populated by
    camicc-fetch-dcps), <repo>/dcps and ~/.cache/camicc/dcps."""
    env = (os.environ.get('CAMICC_DCP_DIR')
           or os.environ.get('DCP2ICC_DCP_DIR'))    # deprecated name
    if env:
        return [Path(env)] if Path(env).is_dir() else []
    cands = [Path('dcps'),
             Path(__file__).resolve().parents[1] / 'dcps',
             Path('~/.cache/camicc/dcps').expanduser(),
             Path('~/.cache/dcp2icc/dcps').expanduser()]  # deprecated
    return [c for c in cands if c.is_dir()]


@functools.lru_cache(maxsize=None)
def _dcp_index():
    idx = {}
    for base in default_dcp_dirs():
        for f in sorted(base.rglob('*.dcp')):
            idx.setdefault(f.name.lower(), f)
    return idx


def match_dcp(jpeg, profile=None):
    """Auto-match the DCP from the default DCP folders. profile, when
    given, is an explicit Adobe profile name (e.g. XMP-crs:CameraProfile
    read from a Lightroom export of the same raw: "Camera Standard") and
    wins over the guess from the camera JPEG's Picture Style
    ('<Model> Camera <Style>.dcp', Auto counts as Standard, fallback
    '<Model> Adobe Standard.dcp'). Returns None when nothing matches."""
    t = exif_tags(jpeg, 'Model', 'PictureStyle')
    model = t.get('Model')
    if not model:
        return None
    if profile:
        hit = _dcp_index().get(f'{model} {profile}.dcp'.lower())
        if hit:
            return hit
    style = t.get('PictureStyle') or 'Standard'
    if style.lower() == 'auto':
        style = 'Standard'
    return (_dcp_index().get(f'{model} camera {style}.dcp'.lower())
            or _dcp_index().get(f'{model} adobe standard.dcp'.lower()))


def camera_neutral(raw):
    """The raw's as-shot camera neutral (raw RGB response to the scene
    white, green-normalized = 1/WB-multipliers), from EXIF. None when no
    usable white balance tag exists."""
    t = exif_tags(raw, 'WB_RGGBLevelsAsShot', 'WB_RGGBLevels', 'AsShotNeutral')
    levels = t.get('WB_RGGBLevelsAsShot') or t.get('WB_RGGBLevels')
    if levels:
        try:
            r, g1, g2, b = (float(v) for v in levels.split())
            g = (g1 + g2) / 2.0
            if r > 0 and b > 0 and g > 0:
                return (g / r, 1.0, g / b)
        except ValueError:
            pass
    neutral = t.get('AsShotNeutral')
    if neutral:
        try:
            vals = [float(v) for v in neutral.split()]
            if len(vals) == 3 and vals[1] > 0:
                return (vals[0] / vals[1], 1.0, vals[2] / vals[1])
        except ValueError:
            pass
    return None


_cct_cache = {}


def shot_cct(raw, dcp_path):
    """Shot color temperature estimated from the raw's as-shot white balance
    through the DCP's color matrices (what Lightroom does before
    interpolating the dual-illuminant tables). None when unavailable."""
    key = (str(raw), str(dcp_path))
    if key not in _cct_cache:
        cct = None
        neutral = camera_neutral(raw)
        if neutral is not None:
            try:
                dcp = parse_dcp(str(dcp_path))
                # profiles that render identically under any illuminant
                # (e.g. Adobe "Camera *": equal forward matrices, no dual
                # HueSatMap) get no CCT so their ICC name/content is stable
                if illuminant_dependent(dcp):
                    cct = estimate_cct(dcp, neutral)
            except (ValueError, np.linalg.LinAlgError):
                cct = None
        _cct_cache[key] = cct
    return _cct_cache[key]


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


RENDER_SIZE = 1280   # px, longest side: the metric works on 480x320, so a
                     # reduced-size export loses nothing and renders 2-3x
                     # faster than the full ~6000 px frame


def run_darktable(raw, xmp, out, configdir):
    # display-referred workflow => temperature module defaults to "as shot"
    # white balance (DCP-derived profiles expect fully white-balanced input);
    # the auto_presets_applied flag in the XMP blocks the other presets.
    cmd = ['darktable-cli', str(raw), str(xmp), str(out),
           '--width', str(RENDER_SIZE), '--height', str(RENDER_SIZE),
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


def tile_size(path, long_side=560):
    """Montage tile size for this image, preserving its aspect ratio."""
    with Image.open(path) as im:
        w, h = ImageOps.exif_transpose(im).size
    s = long_side / max(w, h)
    return (max(1, round(w * s)), max(1, round(h * s)))


def load_rgb_fit(path, box=(560, 420), bg=(30, 30, 30)):
    """Load into a fixed box preserving aspect ratio (letterboxed) — for
    montages that mix landscape and portrait images."""
    im = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    return ImageOps.pad(im, box, Image.LANCZOS, color=bg)


def central_crop(a, frac=0.8):
    """Central frac x frac area of an image array — the metric ignores the
    frame borders, where lens distortion/vignetting differences dominate."""
    h, w = a.shape[:2]
    dy, dx = round(h * (1 - frac) / 2), round(w * (1 - frac) / 2)
    return a[dy:h - dy, dx:w - dx]


def metrics(img, ref):
    a = central_crop(np.asarray(img).astype(float))
    b = central_crop(np.asarray(ref).astype(float))
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


def build_profiles(dcp_path, profdir, cct=None):
    """Convert the DCP into the two ICC variants inside profdir.
    Returns {'camera look': path, 'colors only': path} ('camera look' only
    when the DCP carries a usable tone curve). cct, when given, builds the
    profiles interpolated at that shot color temperature (dual-illuminant
    DCPs only) and suffixes the names with @<K>K."""
    profdir = Path(profdir)
    profdir.mkdir(parents=True, exist_ok=True)
    dcp = parse_dcp(str(dcp_path))
    base = ' '.join(x for x in (dcp.unique_camera_model, dcp.profile_name) if x)
    if cct is not None:
        base += f' @{round(cct)}K'
    variants = {}
    if dcp.tone_curve is not None:
        lab, itab = render_clut(dcp, curve='dcp', curve_mode='channel',
                                cct=cct)
        p = profdir / f'{base} (camera look).icc'
        write_icc(str(p), f'{base} (camera look)', lab, itab, 33)
        variants['camera look'] = p
    lab, itab = render_clut(dcp, curve='none', pre_ev=0.0, cct=cct)
    p = profdir / f'{base} (colors only).icc'
    write_icc(str(p), f'{base} (colors only)', lab, itab, 33)
    variants['colors only'] = p
    return variants


def compare_one(raw, refs, outdir, tonemapper='sigmoid', extras=(),
                cleanup=True, use_cct=True):
    """Run the full comparison for one raw file against one or more
    reference images ("sources of truth").

    refs is a list of (slug, label, path, dcp); the first entry is the
    primary reference (normally the camera JPEG), whose outputs keep the
    canonical names metrics.md / comparison-full.jpg — every further
    reference gets metrics-<slug>.md / comparison-<slug>.jpg. Each
    reference is scored against camicc renders built from ITS OWN dcp
    (references naming the same DCP share renders — the common case);
    the darktable-default and RawTherapee renders are DCP-independent and
    always shared. Each comparison also scores the other references as
    panels.

    Returns {label: rows} with rows = [(name, mean, p95), ...] sorted best
    first. cleanup=True (the default) deletes the intermediate renders,
    XMPs and the darktable config dir afterwards."""
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    lock = out / '.run.lock'
    try:
        lock.touch(exist_ok=False)
    except FileExistsError:
        sys.exit(f'{out}: another comparison seems to be running in this '
                 f'directory (it deletes and rewrites the render files, so '
                 f'concurrent runs corrupt each other). If no other run is '
                 f'active, delete {lock} and retry.')
    cfg = out / 'dtconfig'

    # camicc renders per distinct DCP
    dcps = []                      # distinct dcp paths, first-seen order
    for _, _, _, d in refs:
        if d not in dcps:
            dcps.append(d)
    renders_by_dcp = {}            # dcp -> [(label, path), ...]
    shared = []                    # DCP-independent renders
    for di, dcp_path in enumerate(dcps):
        cct = shot_cct(raw, dcp_path) if use_cct else None
        variants = build_profiles(dcp_path, cfg / 'color' / 'in', cct=cct)
        print(f'built {len(variants)} profile(s) from '
              f'{os.path.basename(str(dcp_path))}'
              + (f' (interpolated at the shot CCT, {cct:.0f}K)'
                 if cct else ''))
        jobs = []
        if 'camera look' in variants:
            jobs.append(('camicc (camera look)',
                         variants['camera look'], False, 0.0))
        jobs.append((f'camicc (colors only)+{tonemapper}',
                     variants['colors only'], True, 0.7))
        mine = []
        for label, icc, tm, ev in jobs:
            stem = label.replace(' ', '_').replace('(', '').replace(')', '')                         .replace('+', '_') + (f'_dcp{di}' if di else '')
            xmp = out / f'{stem}.xmp'; png = out / f'{stem}.png'
            png.unlink(missing_ok=True)
            dtxmp.make_xmp(os.path.basename(str(raw)), str(xmp), str(icc),
                           tm, ev, tonemapper_op=tonemapper)
            run_darktable(raw, xmp, png, cfg)
            mine.append((label, png))
            print(f'rendered: {label}'
                  + (f' [{os.path.basename(str(dcp_path))}]' if di else ''))
        renders_by_dcp[dcp_path] = mine

    label = f'darktable default ({tonemapper})'
    stem = label.replace(' ', '_').replace('(', '').replace(')', '')
    xmp = out / f'{stem}.xmp'; png = out / f'{stem}.png'
    png.unlink(missing_ok=True)
    dtxmp.make_xmp(os.path.basename(str(raw)), str(xmp), None, True, 0.7,
                   tonemapper_op=tonemapper)
    run_darktable(raw, xmp, png, cfg)
    shared.append((label, png))
    print(f'rendered: {label}')

    rt_ref = render_rawtherapee(raw, out)
    if rt_ref is not None:
        shared.append(('RawTherapee (native DCP)', rt_ref))
        print('rendered: RawTherapee (native DCP)')

    renders = [r for mine in renders_by_dcp.values() for r in mine] + shared

    extra_panels = []
    for item in extras:
        name, _, path = item.partition('=')
        extra_panels.append((name, path))

    all_rows = {}
    for i, (slug, ref_label, ref_path, ref_dcp) in enumerate(refs):
        # the other sources of truth are scored/shown as regular panels
        others = [(l, p) for _, l, p, _ in refs if l != ref_label]
        candidates = renders_by_dcp[ref_dcp] + shared + others + extra_panels
        ref = load_rgb(ref_path, METRIC)
        scored = []
        for name, path in candidates:
            m, p95 = metrics(load_rgb(path, METRIC), ref)
            scored.append((name, path, m, p95))
        scored.sort(key=lambda r: r[2])
        rows = [(n, m, p) for n, _, m, p in scored]
        table = [f'| Rendering | mean diff vs {ref_label} | p95 |',
                 '|---|---|---|']
        table += [f'| {n} | {m:.1f} | {p:.0f} |' for n, m, p in rows]
        report = '\n'.join(table)
        print('\n' + report)
        metrics_name = 'metrics.md' if i == 0 else f'metrics-{slug}.md'
        montage_name = ('comparison-full.jpg' if i == 0
                        else f'comparison-{slug}.jpg')
        (out / metrics_name).write_text(report + '\n')

        # montage sorted by similarity: the reference first, then best first
        ts = tile_size(ref_path)
        fs = max(13, min(22, ts[0] // 26))   # narrow portrait tiles
        tiles = [labeled(load_rgb(ref_path, ts), ref_label, fontsize=fs)]
        tiles += [labeled(load_rgb(p, ts), f'{n} - {m:.1f}', fontsize=fs)
                  for n, p, m, _ in scored]
        montage(tiles, cols=3).save(out / montage_name, quality=88)
        all_rows[ref_label] = rows

    if cleanup:
        for _, path in renders:
            Path(path).unlink(missing_ok=True)
            Path(path).with_suffix('.xmp').unlink(missing_ok=True)
        shutil.rmtree(cfg, ignore_errors=True)
    else:
        shutil.rmtree(cfg / 'cache', ignore_errors=True)
    lock.unlink(missing_ok=True)
    return all_rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--raw', required=True, help='raw file (.CR3/.NEF/...)')
    ap.add_argument('--jpeg', required=True, help='out-of-camera JPEG (ground truth)')
    ap.add_argument('--dcp', default=None,
                    help='DCP profile to test (default: auto-matched from '
                         'the JPEG\'s camera model and Picture Style in the '
                         'default DCP folders — see camicc-fetch-dcps)')
    ap.add_argument('--extra', action='append', default=[], metavar='NAME=PATH',
                    help='additional reference image(s) you rendered yourself '
                         'from the same raw in another program, e.g. '
                         'Lightroom=lr.tif (repeatable)')
    ap.add_argument('--tonemapper', choices=sorted(dtxmp.TONEMAPPERS),
                    default=os.environ.get('CAMICC_TONEMAPPER')
                    or os.environ.get('DCP2ICC_TONEMAPPER', 'sigmoid'),
                    help='darktable tone mapper module for the "colors only" '
                         'and default renders: sigmoid (upstream darktable) '
                         'or agx (spektrafilm fork); also settable via '
                         '$CAMICC_TONEMAPPER (default: sigmoid)')
    ap.add_argument('--no-cct', action='store_true',
                    help='disable per-image CCT interpolation of '
                         'dual-illuminant DCPs (then the daylight tables are '
                         'used as-is, the pre-2026-08 behavior)')
    ap.add_argument('--keep', action='store_true',
                    help='keep the intermediate renders/XMPs/dtconfig '
                         '(deleted by default, only the report files remain)')
    ap.add_argument('-o', '--outdir', default='compare-results')
    a = ap.parse_args()

    dcp = a.dcp or match_dcp(a.jpeg)
    if dcp is None:
        sys.exit('no --dcp given and no matching profile in the default DCP '
                 'folders — run camicc-fetch-dcps first (it downloads Adobe '
                 'DNG Converter and extracts all camera profiles)')
    if not a.dcp:
        print(f'auto-matched DCP: {Path(dcp).name}')
    style = picture_style(a.jpeg)
    label = f'Camera JPEG ({style})' if style else 'Camera JPEG'
    compare_one(a.raw, [('camera', label, a.jpeg, dcp)], a.outdir,
                tonemapper=a.tonemapper, extras=a.extra, cleanup=not a.keep,
                use_cct=not a.no_cct)
    print(f'\nresults in {a.outdir}/ (metrics.md, comparison-full.jpg)')


if __name__ == '__main__':
    main()
