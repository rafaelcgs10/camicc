#!/usr/bin/env python3
"""Spike: generate a linear DNG containing a grid of known camera-RGB patches
(with the EOS RP ColorMatrix embedded), render it through the REAL darktable
base stack, and read the patches back => measured F on a dense grid.
"""
import struct, sys, subprocess, json
from pathlib import Path
import numpy as np

sys.path.insert(0, '/home/rafael/Documents/dcp2icc')
sys.path.insert(0, '/home/rafael/Documents/dcp2icc/luts/Canon EOS RP')
from camicc.dcp import parse_dcp
import fitlut

S = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else S / "synthdng"
OUT.mkdir(exist_ok=True)

# ---- patch value grid: WB'd camera RGB -------------------------------------
def patch_values():
    """HSV-ish grid in camera space: hues x sats x log-spaced values,
    plus a neutral ramp. Returns (n,3) float in (0,1]."""
    hh = np.linspace(0, 6, 49)[:-1]
    ss = np.array([0.08, 0.18, 0.30, 0.45, 0.60, 0.75, 0.88, 0.97])
    vv = 0.18 * np.exp2(np.linspace(-6.0, 2.45, 22))
    vv = vv[vv <= 1.0]
    from camicc.pipeline import hsv2rgb
    H, Sm, V = np.meshgrid(hh, ss, vv, indexing='ij')
    rgb = hsv2rgb(H.ravel(), Sm.ravel(), V.ravel())
    neutral = 0.18 * np.exp2(np.linspace(-7.0, 2.45, 40))
    neutral = neutral[neutral <= 1.0][:, None].repeat(3, 1)
    vals = np.concatenate([rgb, neutral], 0)
    return np.clip(vals, 1e-5, 1.0)

# ---- minimal linear DNG writer ---------------------------------------------
def write_dng(path, img16):
    """img16: (H,W,3) uint16. Bare linear DNG, single IFD."""
    H, W, _ = img16.shape
    data = img16.tobytes()

    entries = []
    extra = bytearray()
    header_size = 8
    # we lay out: header(8) | IFD | extra values | pixel data
    def add(tag, typ, count, value_bytes, inline_ok):
        entries.append([tag, typ, count, value_bytes, inline_ok])

    def rat(vals):  # signed rationals, 1e6 denominator
        b = b''
        for v in vals:
            b += struct.pack('<ii', int(round(v * 1000000)), 1000000)
        return b

    cm2 = json.loads(Path(OUT / 'cm2.json').read_text())  # 3x3 row-major
    add(254, 4, 1, struct.pack('<I', 0), True)                    # SubfileType
    add(256, 4, 1, struct.pack('<I', W), True)                    # width
    add(257, 4, 1, struct.pack('<I', H), True)                    # length
    add(258, 3, 3, struct.pack('<HHH', 16, 16, 16) + b'\0\0', False)  # bits
    add(259, 3, 1, struct.pack('<H', 1) + b'\0\0', True)          # no comp
    add(262, 3, 1, struct.pack('<H', 34892) + b'\0\0', True)      # LinearRaw
    add(273, 4, 1, None, True)                                    # strip offset (patched)
    add(277, 3, 1, struct.pack('<H', 3) + b'\0\0', True)          # samples
    add(278, 4, 1, struct.pack('<I', H), True)                    # rows/strip
    add(279, 4, 1, struct.pack('<I', len(data)), True)            # strip bytes
    add(284, 3, 1, struct.pack('<H', 1) + b'\0\0', True)          # planar
    add(50706, 1, 4, bytes([1, 4, 0, 0]), True)                   # DNGVersion
    add(50707, 1, 4, bytes([1, 1, 0, 0]), True)                   # DNGBackward
    add(50708, 2, len(b'SynthGrid\0'), b'SynthGrid\0', False)     # UniqueModel
    add(50714, 3, 3, struct.pack('<HHH', 0, 0, 0) + b'\0\0', False)   # black
    add(50717, 3, 3, struct.pack('<HHH', 65535, 65535, 65535) + b'\0\0', False)  # white
    add(50721, 10, 9, rat(cm2), False)                            # ColorMatrix1
    add(50722, 10, 9, rat(cm2), False)                            # ColorMatrix2
    neut = json.loads(Path(OUT / 'cam_neutral.json').read_text())
    add(50728, 5, 3, rat(neut), False)                            # AsShotNeutral
    add(50778, 3, 1, struct.pack('<H', 21) + b'\0\0', True)       # CalibIllum1 D65
    add(50779, 3, 1, struct.pack('<H', 21) + b'\0\0', True)       # CalibIllum2 D65

    entries.sort(key=lambda e: e[0])
    n = len(entries)
    ifd_size = 2 + n * 12 + 4
    extra_off = header_size + ifd_size
    # first pass: place extras
    placed = []
    for tag, typ, count, vb, inline in entries:
        if tag == 273:
            placed.append((tag, typ, count, None, True))
            continue
        if inline and len(vb) <= 4:
            placed.append((tag, typ, count, vb.ljust(4, b'\0'), True))
        else:
            placed.append((tag, typ, count, struct.pack('<I', extra_off + len(extra)), False))
            extra.extend(vb)
    data_off = extra_off + len(extra)
    out = bytearray()
    out += b'II*\x00' + struct.pack('<I', header_size)
    out += struct.pack('<H', n)
    for tag, typ, count, vb, inline in placed:
        if tag == 273:
            vb = struct.pack('<I', data_off)
        out += struct.pack('<HHI', tag, typ, count) + vb
    out += struct.pack('<I', 0)
    out += extra
    out += data
    Path(path).write_bytes(bytes(out))


def build_grid_dng():
    dcp = parse_dcp('/home/rafael/Documents/dcp2icc/dcps/Adobe Standard/'
                    'Canon EOS RP Adobe Standard.dcp')
    CM2 = np.asarray(dcp.color_matrix_2, float).reshape(3, 3)
    (OUT / 'cm2.json').write_text(json.dumps(list(CM2.reshape(-1))))
    # camera response to D65 white: raw neutrals are NOT (1,1,1) on a real
    # sensor; darktable's camera-reference WB divides by this. Store values
    # scaled by it so dt's WB reconstructs our intended WB'd camRGB exactly.
    D65 = np.array([0.95047, 1.0, 1.08883])
    cam_neutral = CM2 @ D65
    cam_neutral = cam_neutral / cam_neutral.max()
    (OUT / 'cam_neutral.json').write_text(json.dumps(list(cam_neutral)))
    vals = patch_values()
    PS = 40                       # patch size px
    W, H = 6240, 4160
    cols, rows = W // PS, H // PS
    cap = cols * rows
    assert len(vals) <= cap, (len(vals), cap)
    img = np.zeros((H, W, 3), np.uint16)
    for i, v in enumerate(vals):
        r, c = divmod(i, cols)
        y0, x0 = r * PS, c * PS
        img[y0:y0+PS, x0:x0+PS] = np.round(v * cam_neutral * 65535).astype(np.uint16)
    write_dng(OUT / 'grid.dng', img)
    np.save(OUT / 'grid_vals.npy', vals)
    print(f'grid.dng written: {len(vals)} patches ({cols}x{rows} slots)')
    return vals


def render_grid(ev):
    xmp = OUT / 'grid.xmp'
    fitlut.make_xmp('grid.dng', xmp, ev=ev)
    png = OUT / f'grid_{ev:+.3f}.png'
    if png.exists():
        return png
    r = subprocess.run(['darktable-cli', str(OUT / 'grid.dng'), str(xmp), str(png),
        '--core', '--disable-opencl', '--configdir', str(OUT / 'cfg'),
        '--library', ':memory:', '--conf', 'write_sidecar_files=never',
        '--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
        '--conf', 'plugins/darkroom/chromatic-adaptation=modern'],
        capture_output=True, text=True)
    if not png.exists():
        print('STDERR tail:', r.stderr[-1500:])
        sys.exit('render failed')
    return png


def read_patches(png, nvals):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(png).convert('RGB')
    a = np.asarray(im, np.float32) / 255.0
    PS = 40
    W = 6240
    cols = W // PS
    got_h, got_w = a.shape[:2]
    print('render size:', got_w, 'x', got_h)
    sy, sx = got_h / 4160.0, got_w / 6240.0
    med = np.zeros((nvals, 3), np.float32)
    for i in range(nvals):
        r, c = divmod(i, cols)
        y0 = int((r * PS + 10) * sy); y1 = int((r * PS + 30) * sy)
        x0 = int((c * PS + 10) * sx); x1 = int((c * PS + 30) * sx)
        med[i] = np.median(a[y0:y1, x0:x1].reshape(-1, 3), axis=0)
    return med


if __name__ == '__main__':
    vals = build_grid_dng()
    png = render_grid(fitlut.BASE_EV)
    med = read_patches(png, len(vals))
    # sanity: neutral ramp behaves? last 30-40 entries are the neutral ramp
    nneu = len(vals) - int((len(vals) // 100) * 100 == len(vals))
    neu_idx = np.where(np.all(np.abs(vals - vals[:, :1]) < 1e-9, axis=1))[0]
    print('neutral patches:', len(neu_idx))
    for i in neu_idx[::6]:
        print(f'  cam {vals[i,0]:.4f} -> rendered {np.round(med[i],3)}')
    sat = med.max(1) - med.min(1)
    print('mean rendered sat of neutral patches: %.4f (should be ~0)'
          % float(sat[neu_idx].mean()))
    np.save(OUT / 'grid_rendered.npy', med)
    print('OK: measured F saved')
