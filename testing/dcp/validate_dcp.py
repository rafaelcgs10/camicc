#!/usr/bin/env python3
"""Validate darktable's native DCP support against ART (ground truth).

Type A: both WITHOUT tone mapping.
  ART:       Camera Standard DCP, ToneCurve=false, LookTable+HSM on, WB=Camera
  darktable: DCP input profile, no tone mapper, WB + color calibration default

Compared by segment medians + within-segment L distribution (crop-proof),
after a global exposure alignment measured on neutral segments.

Usage: validate_dcp.py --dt-bin <path/darktable-cli> [--tag name]
"""
import argparse, json, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None
REPO = Path('/home/rafael/Documents/dcp2icc')
IMGDIR = REPO / 'testing/Canon EOS RP'
import os as _os
ARTREF = IMGDIR / _os.environ.get('DCP_ARTREF', 'artref')
SEGS = json.load(open(REPO / 'docs/dcp/segments.json'))
SEGS = {k: v for k, v in SEGS.items() if not k.startswith('_')}
IMAGES = _os.environ.get('DCP_IMAGES', 'IMG_8736,IMG_8919,IMG_9029,IMG_9399,19-43-22-103').split(',')
import os as _os2
DCP = _os2.environ.get('DCP_FILE',
      '/home/rafael/Documents/dcp2icc/dcps/Camera/Canon EOS RP/'
      'Canon EOS RP Camera Standard.dcp')


def srgb_lin(x):
    return np.where(x > 0.04045, ((x + 0.055) / 1.055) ** 2.4, x / 12.92)


def srgb_enc(x):
    x = np.clip(x, 0, None)
    return np.where(x > 0.0031308, 1.055 * x ** (1 / 2.4) - 0.055, 12.92 * x)


def to_lab(a):
    a = srgb_lin(a)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = a @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116*f[..., 1] - 16, 500*(f[..., 0]-f[..., 1]),
                     200*(f[..., 1]-f[..., 2])], -1)


def seg_pix(img, rect, step=2):
    w, h = img.size
    box = (int(rect[0]*w), int(rect[1]*h), int(rect[2]*w), int(rect[3]*h))
    a = np.asarray(img.crop(box), np.float32) / 255.0
    return a[::step, ::step].reshape(-1, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dt-bin', default='darktable-cli')
    ap.add_argument('--tag', default='dcp')
    ap.add_argument('--configdir', default=None)
    ap.add_argument('--xmp-only', action='store_true')
    ap.add_argument('--cc', default='on', choices=['on', 'off'],
                    help='color calibration state in the rendered stack')
    ap.add_argument('--unadapt', default='true', choices=['true', 'false'])
    ap.add_argument('--vscale', default='1.0')
    ap.add_argument('--clip', default='false', choices=['true', 'false'])
    ap.add_argument('--tables', default='true', choices=['true', 'false'])
    a = ap.parse_args()
    out = REPO / 'testing/dcp/renders' / a.tag
    out.mkdir(parents=True, exist_ok=True)
    cfg = Path(a.configdir) if a.configdir else out / 'cfg'

    import math
    rows = []
    for name in IMAGES:
        png = out / f'{name}.png'
        xmp = out / f'{name}.xmp'
        write_xmp(xmp, name, a.cc, ev=0.0)
        if not a.xmp_only:
            png.unlink(missing_ok=True)
            r = subprocess.run(
                [a.dt_bin, str(IMGDIR / f'{name}.CR3'), str(xmp), str(png),
                 '--core', '--disable-opencl', '--configdir', str(cfg),
                 '--library', ':memory:',
                 '--conf', 'write_sidecar_files=never',
                 '--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
                 '--conf', 'plugins/darkroom/chromatic-adaptation=modern',
                 '--conf', f'plugins/darkroom/colorin/dcp_unadapt={a.unadapt}',
                 '--conf', f'plugins/darkroom/colorin/dcp_value_scale={a.vscale}',
                 '--conf', f'plugins/darkroom/colorin/dcp_clip={a.clip}',
                 '--conf', f'plugins/darkroom/colorin/dcp_tables={a.tables}'],
                capture_output=True, text=True)
            if not png.exists():
                print(f'RENDER FAILED {name}:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}')
                sys.exit(1)
        art = ImageOps.exif_transpose(
            Image.open(ARTREF / f'{name}.jpg')).convert('RGB')
        dt = ImageOps.exif_transpose(Image.open(png)).convert('RGB')
        if dt.size != art.size:
            dt = dt.resize(art.size, Image.LANCZOS)
        # global exposure alignment on neutral segments
        gains = []
        for sname, sd in SEGS[name].items():
            if sd['class'] != 'neutral':
                continue
            da = srgb_lin(seg_pix(dt, sd['rect'])).mean(0)
            ar = srgb_lin(seg_pix(art, sd['rect'])).mean(0)
            gains.append(float((ar / np.maximum(da, 1e-6)).mean()))
        g = float(np.median(gains)) if gains else 1.0
        if _os.environ.get('DCP_INPIPE') and not a.xmp_only and abs(g - 1) > .02:
            # re-render with the alignment applied in-pipe (exposure module)
            # so highlights are not falsely clipped by the 8-bit export
            write_xmp(xmp, name, a.cc, ev=math.log2(g))
            png.unlink(missing_ok=True)
            subprocess.run(
                [a.dt_bin, str(IMGDIR / f'{name}.CR3'), str(xmp), str(png),
                 '--core', '--disable-opencl', '--configdir', str(cfg),
                 '--library', ':memory:',
                 '--conf', 'write_sidecar_files=never',
                 '--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
                 '--conf', 'plugins/darkroom/chromatic-adaptation=modern',
                 '--conf', f'plugins/darkroom/colorin/dcp_unadapt={a.unadapt}',
                 '--conf', f'plugins/darkroom/colorin/dcp_value_scale={a.vscale}',
                 '--conf', f'plugins/darkroom/colorin/dcp_clip={a.clip}',
                 '--conf', f'plugins/darkroom/colorin/dcp_tables={a.tables}'],
                capture_output=True, text=True)
            dt = ImageOps.exif_transpose(Image.open(png)).convert('RGB')
            if dt.size != art.size:
                dt = dt.resize(art.size, Image.LANCZOS)
            gains = []
            for sname, sd in SEGS[name].items():
                if sd['class'] != 'neutral':
                    continue
                da = srgb_lin(seg_pix(dt, sd['rect'])).mean(0)
                ar = srgb_lin(seg_pix(art, sd['rect'])).mean(0)
                gains.append(float((ar / np.maximum(da, 1e-6)).mean()))
            g = float(np.median(gains)) if gains else 1.0
        D = np.asarray(dt, np.float32) / 255.0
        Dal = Image.fromarray(
            (np.clip(srgb_enc(srgb_lin(D) * g), 0, 1) * 255).astype(np.uint8))
        segd, spread = [], []
        for sname, sd in SEGS[name].items():
            p1 = seg_pix(Dal, sd['rect'])
            p2 = seg_pix(art, sd['rect'])
            m1, m2 = np.median(p1, 0), np.median(p2, 0)
            d = float(np.sqrt(((to_lab(m1[None]) - to_lab(m2[None]))**2).sum()))
            L1, L2 = to_lab(p1)[:, 0], to_lab(p2)[:, 0]
            sp = abs(float(np.percentile(L1, 90) - np.percentile(L1, 10))
                     - float(np.percentile(L2, 90) - np.percentile(L2, 10)))
            segd.append((sname, sd['class'], d, sp))
            spread.append(sp)
        mean_d = float(np.mean([s[2] for s in segd]))
        rows.append((name, g, mean_d, float(np.mean(spread)), segd))
        print(f'{name:16s} gain {g:5.3f}  segment dE {mean_d:6.2f}  '
              f'spread err {np.mean(spread):5.2f}')
    print(f'{"MEAN":16s} {"":10s} segment dE {np.mean([r[2] for r in rows]):6.2f}  '
          f'spread err {np.mean([r[3] for r in rows]):5.2f}')
    print('\nworst segments:')
    allsegs = [(r[0], *s) for r in rows for s in r[4]]
    for n, sname, cls, d, sp in sorted(allsegs, key=lambda x: -x[3])[:10]:
        print(f'  {n}/{sname} [{cls}]: dE {d:.2f} (spread err {sp:.1f})')
    json.dump({'rows': [(r[0], r[1], r[2], r[3]) for r in rows],
               'segments': allsegs}, open(out / 'scores.json', 'w'), indent=1)


def write_xmp(path, name, cc='on', ev=0.0):
    """darktable stack: DCP input profile, NO tone mapper, defaults elsewhere."""
    import struct, zlib, base64, math

    def enc(raw):
        c = zlib.compress(raw, 9)
        return 'gz%02d' % min(99, max(1, math.ceil(len(raw)/len(c)))) + \
            base64.b64encode(c).decode()

    # colorin v7 with DT_COLORSPACE_DCP (type id read from the build; 30 by
    # convention here — validate_dcp.py is regenerated if the id changes)
    TYPE_DCP = int(__import__('os').environ.get('DT_DCP_TYPE', '30'))
    raw = (struct.pack('<i', TYPE_DCP) + DCP.encode().ljust(512, b'\0')
           + struct.pack('<iiii', 0, 0, 0, 4) + b'\0' * 512)
    colorin = enc(raw)
    BLEND = 'gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU='
    expo = struct.pack('<iffff', 0, -0.000244140625, float(ev), 50.0,
                       -4.0).hex() + '0100000001000000'
    lens = enc(struct.pack('<iii', 0, 7, 0)
               + struct.pack('<5f', 1.0, 0, 0, 0, 0) + struct.pack('<i', 1)
               + b'\0'*256 + struct.pack('<i', 0) + struct.pack('<2f', 1, 1)
               + struct.pack('<4f', 1, 1, 1, 1) + struct.pack('<f', 1)
               + struct.pack('<i', 1) + struct.pack('<f', 1)
               + struct.pack('<i', 0) + struct.pack('<3f', 0, .5, .5)
               + struct.pack('<2f', 0, 0))
    # channelmixerrgb v3, "as shot in camera" — darktable's modern default
    CHMIX = 'gz04eJxjYGiwZ8AAxIqRD9iBmAmIWaDYbd8uO+sFh+30Zna7guxihMoDAKRhCIA='
    # highlights v4, method=clip — pin both engines to plain clipping when
    # DCP_HL=clip (pipeline-matched comparisons)
    hl = enc(struct.pack('<ifff', 0, 1.0, 0.0, 0.0)      # mode, blendL, blendC, strength
             + struct.pack('<f', 1.0)                     # clip
             + struct.pack('<fii', 0.0, 30, 6)            # noise, iterations, scales
             + struct.pack('<ffi', 0.4, 2.0, 0)           # candidating, combine, recovery
             + struct.pack('<f', 0.0))                    # solid_color
    hl_on = 1 if __import__('os').environ.get('DCP_HL') == 'clip' else None
    ops = [('colorin', 1, 7, colorin),
           ('channelmixerrgb', 1 if cc == 'on' else 0, 3, CHMIX),
           ('exposure', 1, 7, expo),
           ('lens', 0 if __import__('os').environ.get('DCP_NOLENS') else 1,
            10, lens)] + \
        ([('highlights', 1, 4, hl)] if hl_on else [])
    items = []
    for i, (op, en, ver, p) in enumerate(ops):
        items.append(f'''     <rdf:li
      darktable:num="{i}"
      darktable:operation="{op}"
      darktable:enabled="{en}"
      darktable:modversion="{ver}"
      darktable:params="{p}"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="{BLEND}"/>''')
    Path(path).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"\n'
        '    xmlns:darktable="http://darktable.sf.net/"\n'
        f'   xmpMM:DerivedFrom="{name}.CR3"\n'
        '   darktable:xmp_version="5" darktable:raw_params="0"\n'
        '   darktable:auto_presets_applied="1"\n'
        f'   darktable:history_end="{len(ops)}" darktable:iop_order_version="4">\n'
        '   <darktable:masks_history><rdf:Seq/></darktable:masks_history>\n'
        '   <darktable:history><rdf:Seq>\n' + '\n'.join(items) +
        '\n   </rdf:Seq></darktable:history>\n'
        '  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n')


if __name__ == '__main__':
    main()
