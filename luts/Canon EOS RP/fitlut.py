#!/usr/bin/env python3
"""Fit the "EOS RP -> Lightroom match" 3D LUT from raw/Lightroom pairs.

Self-contained: needs only numpy, Pillow and a darktable-cli on PATH
(use the SAME darktable build you edit with -- the LUT bakes the build's
rendering behavior).

Recipe (each detail exists because skipping it produced a measured
artifact -- see GUIDE.md):

  1. Every raw with a `lightroom_<rawstem>.jpg` next to it becomes a
     training pair. The base rendering is: standard input matrix ->
     exposure -> NEUTRAL agx (default curve, look neutral, primaries
     adjustments DISABLED -- an aggressive base look collapses distinct
     raw colors into one rendered color and the LUT cannot separate them
     again; the base's job is to be invertible, the LUT does the look).
  2. Per-image EXPOSURE alignment, in-pipe: the base is first rendered at
     the default EV, a scalar gain to the Lightroom render is measured on
     near-neutral midtones, and the base is re-rendered at the aligned EV.
     Lightroom's per-image brightness varies a lot (+0.7..+1.3 EV on the
     test set); fitting unaligned pairs bakes contradictory brightness
     votes into color cells (bright content of one image bleaches dark
     content of another -- the "hair artifact"). The user replicates the
     alignment naturally: apply the style, adjust exposure to taste.
     The dt-vs-Lightroom white-balance tint is NOT aligned away -- it is
     consistent across images and the LUT learns it.
  3. Edge/mixture pixels are trained at low weight (0.12): they pair
     unreliably between the two renderers (different sharpening) and
     mottle the sparse mixture cells at full weight.
  4. Fit at FULL resolution (downscaled pairs smear thin structures into
     mixture colors that never existed).
  5. 33^3 trilinear splat + identity prior + confidence-weighted fill,
     then a gentle confidence-weighted global smoothing pass.

More pairs = better generalization to unseen colors. Export from
Lightroom with profile "Camera Standard" and every adjustment zeroed,
as sRGB JPEG.

Usage:
    python3 fitlut.py --imgdir "testing/Canon EOS RP" [--imgdir more ...]
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = None

N = 33
PRIOR = 25.0
EDGE_WEIGHT = 0.12
HERE = Path(__file__).resolve().parent
BASE_EV = 0.5125

# ---------------------------------------------------------------- packers
def enc(raw: bytes) -> str:
    comp = zlib.compress(raw, 9)
    # darktable reads exactly two factor digits and grows the buffer on
    # Z_BUF_ERROR, so the factor must be capped at 99
    factor = min(99, max(1, math.ceil(len(raw) / len(comp))))
    return 'gz%02d' % factor + base64.b64encode(comp).decode()


# the NEUTRAL base: agx defaults, look neutral, primaries adjustments OFF
# (maximally invertible; the LUT does the whole look)
EXPOSURE_EV = BASE_EV
AGX = [0.0, 1.0, 1.0, 1.0, 0.0,               # look: neutral
       -10.0, 6.5, 0.1,                        # log range
       0.606060606061, 0.18, 2.4, 0.0, 0.0,    # pivot_x/pivot_y/contrast/lin
       1.5, 1.5, 2.2]                          # toe/shoulder/gamma
AGX_PRIM = [2, 1,                              # rec2020, adjustments DISABLED
            0.29462, 0.03540, 0.25862, -0.02109, 0.14641, -0.06306,
            1.0, 0.0,
            0.29078, 0.03540, 0.26316, -0.02109, 0.04581, -0.06306]

COLORIN_STANDARD_MATRIX = 'gz48eJzjZhgFowABWAbaAaNgwAEAOQAAEA=='
CHMIX_PARAMS = 'gz04eJxjYGiwZ8AAxIqRD9iBmAmIWaDYbd8uO+sFh+30Zna7guxihMoDAKRhCIA='
SIGMOID_OFF = ('gz03eNpjYDhgzwAGJ5xyOCttYGwGAgAAuegEPg==', 3)
BLEND_DEFAULT = 'gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU='


def exposure_params(ev):
    raw = struct.pack('<iffff', 0, -0.000244140625, ev, 50.0, -4.0)
    return raw.hex() + '0100000001000000'


def agx_params():
    raw = struct.pack('<16f', *AGX) + struct.pack('<i', 0)
    raw += struct.pack('<2f', 0.0, 1.0)
    raw += struct.pack('<2i', AGX_PRIM[0], AGX_PRIM[1])
    raw += struct.pack('<14f', *AGX_PRIM[2:])
    raw += struct.pack('<i', 0)
    return enc(raw)


def lens_params():
    return enc(struct.pack('<iii', 0, 7, 0)
               + struct.pack('<5f', 1.0, 0.0, 0.0, 0.0, 0.0)
               + struct.pack('<i', 1) + b'\0' * 256
               + struct.pack('<i', 0) + struct.pack('<2f', 1.0, 1.0)
               + struct.pack('<4f', 1.0, 1.0, 1.0, 1.0)
               + struct.pack('<f', 1.0) + struct.pack('<i', 1)
               + struct.pack('<f', 1.0) + struct.pack('<i', 0)
               + struct.pack('<3f', 0.0, 0.5, 0.5)
               + struct.pack('<2f', 0.0, 0.0))


def make_xmp(raw_name, out_path, ev=None):
    ops = [
        ('colorin', 1, 7, COLORIN_STANDARD_MATRIX),
        ('channelmixerrgb', 1, 3, CHMIX_PARAMS),
        ('exposure', 1, 7, exposure_params(EXPOSURE_EV if ev is None else ev)),
        ('sigmoid', 0, SIGMOID_OFF[1], SIGMOID_OFF[0]),
        ('agx', 1, 7, agx_params()),
        ('lens', 1, 10, lens_params()),
    ]
    items = []
    for i, (op, en, ver, params) in enumerate(ops):
        items.append(f'''     <rdf:li
      darktable:num="{i}"
      darktable:operation="{op}"
      darktable:enabled="{en}"
      darktable:modversion="{ver}"
      darktable:params="{params}"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="{BLEND_DEFAULT}"/>''')
    body = '\n'.join(items)
    Path(out_path).write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:darktable="http://darktable.sf.net/"
   xmpMM:DerivedFrom="{raw_name}"
   darktable:xmp_version="5"
   darktable:raw_params="0"
   darktable:auto_presets_applied="1"
   darktable:history_end="{len(ops)}"
   darktable:iop_order_version="4">
   <darktable:masks_history>
    <rdf:Seq/>
   </darktable:masks_history>
   <darktable:history>
    <rdf:Seq>
{body}
    </rdf:Seq>
   </darktable:history>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
''')


# ------------------------------------------------------------ color utils
def srgb_lin(x):
    return np.where(x > 0.04045, ((x + 0.055) / 1.055) ** 2.4, x / 12.92)


def srgb_enc_(x):
    x = np.clip(x, 0, None)
    return np.where(x > 0.0031308, 1.055 * x ** (1 / 2.4) - 0.055, 12.92 * x)


def srgb_to_lab(a01):
    a = srgb_lin(a01)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = a @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def dE(a, b):
    return float(np.sqrt(((srgb_to_lab(a) - srgb_to_lab(b)) ** 2)
                         .sum(-1)).mean())


# ------------------------------------------------------------ pairs & fit
def render_base(raw: Path, workdir: Path, ev: float, tag: str) -> Path:
    out = workdir / f'{raw.stem}_{tag}.png'
    if out.exists():
        return out
    xmp = workdir / f'{raw.stem}_{tag}.xmp'
    make_xmp(raw.name, xmp, ev=ev)
    r = subprocess.run(
        ['darktable-cli', str(raw), str(xmp), str(out),
         '--core', '--disable-opencl',
         '--configdir', str(workdir / 'cfg'), '--library', ':memory:',
         '--conf', 'write_sidecar_files=never',
         '--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
         '--conf', 'plugins/darkroom/chromatic-adaptation=modern'],
        capture_output=True, text=True)
    if not out.exists():
        sys.exit(f'darktable-cli failed for {raw}:\n{r.stderr[-800:]}')
    return out


def load_ref(ref: Path, size):
    r = ImageOps.exif_transpose(Image.open(ref)).convert('RGB')
    if r.size != size:
        r = r.resize(size, Image.LANCZOS)
    return r


def aligned_ev(raw: Path, ref: Path, workdir: Path) -> float:
    """Scalar exposure aligning the base to the Lightroom render, measured
    on near-neutral midtones (linear light)."""
    base = ImageOps.exif_transpose(
        Image.open(render_base(raw, workdir, BASE_EV, 'base0'))).convert('RGB')
    refim = load_ref(ref, base.size)
    la = srgb_lin(np.asarray(base, np.float32) / 255.0)
    lb = srgb_lin(np.asarray(refim, np.float32) / 255.0)
    b01 = np.asarray(refim, np.float32) / 255.0
    lum = lb.mean(-1)
    sat = b01.max(-1) - b01.min(-1)
    m = (sat < 0.10) & (lum > 0.05) & (lum < 0.7)
    if m.sum() < 2000:
        m = (sat < 0.2) & (lum > 0.03) & (lum < 0.8)
    g = float((np.median(lb[m], 0)
               / np.maximum(np.median(la[m], 0), 1e-6)).mean())
    return BASE_EV + math.log2(min(max(g, 0.5), 2.0))


def accumulate(raw: Path, ref: Path, workdir: Path, ev: float, acc, wacc):
    base = ImageOps.exif_transpose(
        Image.open(render_base(raw, workdir, ev, 'baseA'))).convert('RGB')
    refim = load_ref(ref, base.size)
    a = np.asarray(base, np.float32) / 255.0
    b = np.asarray(refim, np.float32) / 255.0
    g = np.asarray(base.convert('L'), np.float32)
    gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
    gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
    edges = Image.fromarray(((gx + gy) > 8).astype(np.uint8) * 255) \
        .filter(ImageFilter.MaxFilter(3))
    flat = np.asarray(edges) == 0
    h, w = a.shape[:2]
    dy, dx = round(h * 0.05), round(w * 0.05)
    sl = np.s_[dy:h-dy:2, dx:w-dx:2]
    av = a[sl].reshape(-1, 3)
    bv = b[sl].reshape(-1, 3)
    wp = np.where(flat[sl].reshape(-1), 1.0, EDGE_WEIGHT).astype(np.float32)
    gc = av * (N - 1)
    i0 = np.clip(np.floor(gc).astype(int), 0, N - 2)
    f = gc - i0
    for dz in (0, 1):
        for dyy in (0, 1):
            for dxx in (0, 1):
                wgt = wp * (np.abs(1 - dz - f[:, 0])
                            * np.abs(1 - dyy - f[:, 1])
                            * np.abs(1 - dxx - f[:, 2]))
                idx = (i0[:, 0] + dz, i0[:, 1] + dyy, i0[:, 2] + dxx)
                np.add.at(acc, idx, bv * wgt[:, None])
                np.add.at(wacc, idx, wgt)


def solve_lut(acc, wacc):
    ax = np.linspace(0, 1, N)
    R, G, B = np.meshgrid(ax, ax, ax, indexing='ij')
    ident = np.stack([R, G, B], -1)
    lut = (acc + PRIOR * ident) / (wacc[..., None] + PRIOR)
    conf = wacc / (wacc + PRIOR)
    delta = lut - ident
    for _ in range(16):     # confidence-weighted fill of sparse cells
        sm = np.zeros_like(delta)
        cnt = np.zeros((N, N, N, 1))
        for axis in range(3):
            for sgn in (1, -1):
                sm += np.roll(delta, sgn, axis=axis) \
                    * np.roll(conf, sgn, axis=axis)[..., None]
                cnt += np.roll(conf, sgn, axis=axis)[..., None]
        alpha = (1.0 - conf)[..., None] * 0.7
        delta = delta * (1 - alpha) + (sm / np.maximum(cnt, 1e-9)) * alpha
    for _ in range(6):      # gentle global pass, barely moves trained cells
        sm = np.zeros_like(delta)
        for axis in range(3):
            for sgn in (1, -1):
                sm += np.roll(delta, sgn, axis=axis)
        alpha = (0.10 * (1.0 - conf) + 0.03)[..., None]
        delta = delta * (1 - alpha) + (sm / 6.0) * alpha
    return np.clip(ident + delta, 0, 1)


def write_cube(lut, path, title):
    with open(path, 'w') as f:
        f.write(f'TITLE "{title[:240]}"\nLUT_3D_SIZE {N}\n')
        for b in range(N):
            for g in range(N):
                for r in range(N):
                    v = lut[r, g, b]
                    f.write(f'{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n')
    print('wrote', path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--imgdir', action='append', required=True,
                    help='folder(s) with raws + lightroom_<stem>.jpg pairs')
    ap.add_argument('--workdir', default=None,
                    help='render cache dir (default: temp)')
    a = ap.parse_args()
    workdir = Path(a.workdir) if a.workdir else \
        Path(tempfile.mkdtemp(prefix='fitlut-'))
    workdir.mkdir(parents=True, exist_ok=True)

    pairs = []
    for d in a.imgdir:
        for ref in sorted(Path(d).glob('lightroom_*.jpg')):
            stem = ref.stem[len('lightroom_'):]
            raws = [p for p in Path(d).iterdir()
                    if p.stem == stem and p.suffix.lower() in
                    ('.cr3', '.cr2', '.dng', '.nef', '.arw', '.raf', '.orf')]
            if raws:
                pairs.append((raws[0], ref))
    if len(pairs) < 2:
        sys.exit('need at least 2 raw/lightroom pairs')

    print(f'{len(pairs)} pairs; pass 1: per-image exposure alignment...')
    evs = {}
    for raw, ref in pairs:
        evs[raw.stem] = aligned_ev(raw, ref, workdir)
        print(f'  {raw.name}: aligned EV {evs[raw.stem]:+.3f}')
    json.dump(evs, open(workdir / 'aligned_evs.json', 'w'), indent=1)
    print('style-default suggestion: EV %+.2f (mean)' %
          (sum(evs.values()) / len(evs)))

    print('pass 2: aligned renders + fit...')
    acc = np.zeros((N, N, N, 3))
    wacc = np.zeros((N, N, N))
    for raw, ref in pairs:
        accumulate(raw, ref, workdir, evs[raw.stem], acc, wacc)
        print(f'  {raw.name}: accumulated')
    lut = solve_lut(acc, wacc)
    write_cube(lut, HERE / 'EOS RP Lightroom match.cube',
               'Canon EOS RP -> Lightroom Camera Standard match; apply via '
               'lut3d (sRGB) after the neutral exposure+agx base (see the '
               'style); set exposure per image to taste.')


if __name__ == '__main__':
    main()
