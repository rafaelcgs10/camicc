#!/usr/bin/env python3
"""Fit the "EOS RP -> Lightroom match" 3D LUTs from raw/Lightroom pairs.

Self-contained: needs only numpy, Pillow and a darktable-cli on PATH.

For every raw with a `lightroom_<rawstem>.jpg` next to it, the base
rendering (standard matrix -> exposure +0.51 EV -> fixed agx tone) is
produced with YOUR darktable-cli, and a 33^3 display-referred LUT is
fitted mapping base -> Lightroom, plus a neutral-aligned variant (each
pair first normalized on near-neutral midtones, standing in for your
per-image WB/exposure tweak). Writes both .cube files next to this
script and prints in-sample + leave-one-out scores.

More pairs = better generalization. Export from Lightroom with profile
"Camera Standard" and every adjustment zeroed, as sRGB JPEG.

Usage:
    python3 fitlut.py --imgdir "testing/Canon EOS RP" [--imgdir more ...]
"""
from __future__ import annotations

import argparse
import base64
import math
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

N = 33
PRIOR = 25.0
SIZE = 1200
HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- packers
def enc(raw: bytes) -> str:
    comp = zlib.compress(raw, 9)
    # darktable reads exactly two factor digits and grows the buffer on
    # Z_BUF_ERROR, so the factor must be capped at 99
    factor = min(99, max(1, math.ceil(len(raw) / len(comp))))
    return 'gz%02d' % factor + base64.b64encode(comp).decode()


# the fitted tone base (= the shipped style): exposure + agx v4 params
EXPOSURE_EV = 0.5125
AGX = [0.0, 1.0, 1.0, 0.75, 0.7,              # look lift/slope/bright/sat/hue-mix
       -10.0, 6.5, 0.1,                        # log range
       0.606060606061, 0.16, 4.05, 0.0, 0.0,   # pivot_x/pivot_y/contrast/lin
       1.65, 2.1, 2.2]                         # toe/shoulder/gamma
AGX_TAIL_I = [0, ]                             # auto_gamma
AGX_TAIL_F = [0.0, 1.0]                        # target black/white
AGX_PRIM = [2, 0,                              # base primaries rec2020, enabled
            0.49462, 0.0204, 0.23362, 0.023909999999999997,
            0.12141000000000003, -0.018060000000000007,
            1.0, 0.0,
            0.26578, 0.0204, 0.23816, 0.023909999999999997,
            0.09581, -0.018060000000000007]

COLORIN_STANDARD_MATRIX = 'gz48eJzjZhgFowABWAbaAaNgwAEAOQAAEA=='
CHMIX_PARAMS = 'gz04eJxjYGiwZ8AAxIqRD9iBmAmIWaDYbd8uO+sFh+30Zna7guxihMoDAKRhCIA='
SIGMOID_OFF = ('gz03eNpjYDhgzwAGJ5xyOCttYGwGAgAAuegEPg==', 3)
BLEND_DEFAULT = 'gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU='


def exposure_params(ev):
    raw = struct.pack('<iffff', 0, -0.000244140625, ev, 50.0, -4.0)
    return raw.hex() + '0100000001000000'


def agx_params():
    raw = struct.pack('<16f', *AGX) + struct.pack('<i', *AGX_TAIL_I)
    raw += struct.pack('<2f', *AGX_TAIL_F)
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


def make_xmp(raw_name, out_path):
    ops = [
        ('colorin', 1, 7, COLORIN_STANDARD_MATRIX),
        ('channelmixerrgb', 1, 3, CHMIX_PARAMS),
        ('exposure', 1, 7, exposure_params(EXPOSURE_EV)),
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
def render_base(raw: Path, workdir: Path) -> Path:
    out = workdir / f'{raw.stem}_base.png'
    if out.exists():
        return out
    xmp = workdir / f'{raw.stem}.xmp'
    make_xmp(raw.name, xmp)
    r = subprocess.run(
        ['darktable-cli', str(raw), str(xmp), str(out),
         '--width', str(SIZE), '--height', str(SIZE),
         '--core', '--disable-opencl',
         '--configdir', str(workdir / 'cfg'), '--library', ':memory:',
         '--conf', 'write_sidecar_files=never',
         '--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
         '--conf', 'plugins/darkroom/chromatic-adaptation=modern'],
        capture_output=True, text=True)
    if not out.exists():
        sys.exit(f'darktable-cli failed for {raw}:\n{r.stderr[-800:]}')
    return out


def load_pair(raw: Path, ref: Path, workdir: Path):
    r = ImageOps.exif_transpose(Image.open(ref)).convert('RGB')
    s = SIZE / max(r.size)
    r = r.resize((round(r.width * s), round(r.height * s)), Image.LANCZOS)
    b = ImageOps.exif_transpose(
        Image.open(render_base(raw, workdir))).convert('RGB')
    if b.size != r.size:
        b = b.resize(r.size, Image.LANCZOS)
    a = np.asarray(b, float) / 255.0
    t = np.asarray(r, float) / 255.0
    # edge weighting: edge/mixture pixels (hair strands etc.) pair unreliably
    # between the two renderers (different sharpening) and cause LUT mottle
    # when trained at full weight
    g = np.asarray(b.convert('L'), float)
    gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
    gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
    edges = Image.fromarray(((gx + gy) > 6).astype(np.uint8) * 255) \
        .filter(ImageFilter.MaxFilter(5))
    flat = np.asarray(edges) == 0
    h, w = a.shape[:2]
    dy, dx = round(h * 0.1), round(w * 0.1)
    sl = np.s_[dy:h-dy, dx:w-dx]
    wpix = np.where(flat[sl].reshape(-1), 1.0, 0.12)
    return (a[sl].reshape(-1, 3), t[sl].reshape(-1, 3), wpix)


def align(a, b):
    la, lb = srgb_lin(a), srgb_lin(b)
    lum = lb.mean(-1)
    sat = b.max(-1) - b.min(-1)
    m = (sat < 0.10) & (lum > 0.05) & (lum < 0.7)
    if m.sum() < 2000:
        m = (sat < 0.2) & (lum > 0.03) & (lum < 0.8)
    g = np.median(lb[m], axis=0) / np.maximum(np.median(la[m], axis=0), 1e-6)
    expo = float(np.clip(g.mean(), 0.5, 2.0))
    tint = np.clip(g / g.mean(), 0.85, 1.2)
    return np.clip(srgb_enc_(la * expo * tint), 0.0, 1.0)


def fit_lut(pairs):
    acc = np.zeros((N, N, N, 3))
    wacc = np.zeros((N, N, N))
    for a, b, wp in pairs:
        g = a * (N - 1)
        i0 = np.clip(np.floor(g).astype(int), 0, N - 2)
        f = g - i0
        for dz in (0, 1):
            for dy in (0, 1):
                for dx in (0, 1):
                    w = wp * (np.abs(1 - dz - f[:, 0])
                              * np.abs(1 - dy - f[:, 1])
                              * np.abs(1 - dx - f[:, 2]))
                    idx = (i0[:, 0] + dz, i0[:, 1] + dy, i0[:, 2] + dx)
                    np.add.at(acc, idx, b * w[:, None])
                    np.add.at(wacc, idx, w)
    ax = np.linspace(0, 1, N)
    R, G, B = np.meshgrid(ax, ax, ax, indexing='ij')
    ident = np.stack([R, G, B], -1)
    lut = (acc + PRIOR * ident) / (wacc[..., None] + PRIOR)
    conf = wacc / (wacc + PRIOR)
    delta = lut - ident
    for _ in range(16):
        sm = np.zeros_like(delta)
        cnt = np.zeros((N, N, N, 1))
        for s in in_axes():
            sm += np.roll(delta, s[1], axis=s[0]) \
                * np.roll(conf, s[1], axis=s[0])[..., None]
            cnt += np.roll(conf, s[1], axis=s[0])[..., None]
        neigh = sm / np.maximum(cnt, 1e-9)
        alpha = (1.0 - conf)[..., None] * 0.7
        delta = delta * (1 - alpha) + neigh * alpha
    # gentle confidence-weighted global pass: sparse cells smooth strongly,
    # well-trained cells barely move (kills strand mottle, keeps fidelity)
    for _ in range(6):
        sm = np.zeros_like(delta)
        for s in in_axes():
            sm += np.roll(delta, s[1], axis=s[0])
        neigh = sm / 6.0
        alpha = (0.10 * (1.0 - conf) + 0.03)[..., None]
        delta = delta * (1 - alpha) + neigh * alpha
    return np.clip(ident + delta, 0, 1)


def in_axes():
    return [(a, s) for a in range(3) for s in (1, -1)]


def apply_lut(lut, a):
    g = a * (N - 1)
    i0 = np.clip(np.floor(g).astype(int), 0, N - 2)
    f = g - i0
    out = np.zeros_like(a)
    for dz in (0, 1):
        for dy in (0, 1):
            for dx in (0, 1):
                w = (np.abs(1 - dz - f[:, 0]) * np.abs(1 - dy - f[:, 1])
                     * np.abs(1 - dx - f[:, 2]))
                out += lut[i0[:, 0] + dz, i0[:, 1] + dy, i0[:, 2] + dx] \
                    * w[:, None]
    return out


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

    pairs = {}
    for d in a.imgdir:
        for ref in sorted(Path(d).glob('lightroom_*.jpg')):
            stem = ref.stem[len('lightroom_'):]
            raws = [p for p in Path(d).iterdir()
                    if p.stem == stem and p.suffix.lower() in
                    ('.cr3', '.cr2', '.dng', '.nef', '.arw', '.raf', '.orf')]
            if raws:
                print('pair:', raws[0].name)
                pairs[stem] = load_pair(raws[0], ref, workdir)
    if len(pairs) < 2:
        sys.exit('need at least 2 raw/lightroom pairs')

    print(f'\n{len(pairs)} pairs; fitting...')
    lut_plain = fit_lut(list(pairs.values()))
    apairs = {k: (align(x, y), y, w) for k, (x, y, w) in pairs.items()}
    lut_aligned = fit_lut(list(apairs.values()))

    print('\nin-sample dE76 (plain | aligned, flat pixels):')
    for k in pairs:
        a0, b0, w0 = pairs[k]
        m = w0 > 0.5
        a0, b0 = a0[m], b0[m]
        a1, b1, w1 = apairs[k]
        a1, b1 = a1[m], b1[m]
        print(f'  {k:16s} {dE(apply_lut(lut_plain, a0), b0):5.2f} | '
              f'{dE(apply_lut(lut_aligned, a1), b1):5.2f}')
    if len(pairs) > 2:
        print('leave-one-out dE76 (plain | aligned):')
        for held in pairs:
            lp = fit_lut([v for k, v in pairs.items() if k != held])
            la_ = fit_lut([v for k, v in apairs.items() if k != held])
            a0, b0, w0 = pairs[held]
            m = w0 > 0.5
            a0, b0 = a0[m], b0[m]
            a1, b1, w1 = apairs[held]
            a1, b1 = a1[m], b1[m]
            print(f'  {held:16s} {dE(apply_lut(lp, a0), b0):5.2f} | '
                  f'{dE(apply_lut(la_, a1), b1):5.2f}')

    write_cube(lut_plain, HERE / 'EOS RP Lightroom match.cube',
               'Canon EOS RP -> Lightroom Camera Standard match; apply via '
               'lut3d (sRGB) after the fixed exposure+agx base (see style).')
    write_cube(lut_aligned,
               HERE / 'EOS RP Lightroom match (neutral-aligned).cube',
               'Neutral-aligned variant: assumes per-image WB/exposure '
               'fine-tuning; generalizes better to new scenes.')


if __name__ == '__main__':
    main()
