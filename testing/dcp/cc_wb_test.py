#!/usr/bin/env python3
"""A/B test: with a DCP input profile and color calibration ON, does
changing CC's illuminant (as-shot-in-camera vs daylight 3500K) shift the
render? (Regression for CC-as-creative-WB with the exact-inverse cast
handover: colorin's inverse is built at the camera illuminant, so user
CC edits must remain visible, not cancel out.)"""
import base64, math, struct, subprocess, sys, zlib
from pathlib import Path
import numpy as np
from PIL import Image

DT = Path.home() / 'Documents/darktable-dcp/build/bin/darktable-cli'
RAW = Path.home() / 'Documents/dcp2icc/testing/Canon EOS RP/IMG_8736.CR3'
DCP = (Path.home() / 'Documents/dcp2icc/dcps/Camera/Canon EOS RP/'
       'Canon EOS RP Camera Standard.dcp')
OUT = Path(__file__).parent / 'renders' / 'cc_wb'
OUT.mkdir(parents=True, exist_ok=True)


def enc(raw):
    c = zlib.compress(raw, 9)
    return 'gz%02d' % min(99, max(1, math.ceil(len(raw) / len(c)))) + \
        base64.b64encode(c).decode()


BLEND = 'gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU='


def write_xmp(path, wb):
    colorin = enc(struct.pack('<i', 27) + str(DCP).encode().ljust(512, b'\0')
                  + struct.pack('<iiii', 0, 0, 0, 4) + b'\0' * 512)
    ops = [('colorin', 1, 7, colorin),
           ('channelmixerrgb', 1, 3, wb)]
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
        f'   xmpMM:DerivedFrom="{RAW.name}"\n'
        '   darktable:xmp_version="5" darktable:raw_params="0"\n'
        '   darktable:auto_presets_applied="1"\n'
        f'   darktable:history_end="{len(ops)}" darktable:iop_order_version="4">\n'
        '   <darktable:masks_history><rdf:Seq/></darktable:masks_history>\n'
        '   <darktable:history><rdf:Seq>\n' + '\n'.join(items) +
        '\n   </rdf:Seq></darktable:history>\n'
        '  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n')


def render(tag, wb):
    xmp = OUT / f'{tag}.xmp'
    png = OUT / f'{tag}.png'
    png.unlink(missing_ok=True)
    write_xmp(xmp, wb)
    r = subprocess.run(
        [str(DT), str(RAW), str(xmp), str(png), '--width', '800',
         '--core', '--disable-opencl', '--configdir', str(OUT / 'cfg'),
         '--library', ':memory:', '--conf', 'write_sidecar_files=never',
         '--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
         '--conf', 'plugins/darkroom/chromatic-adaptation=modern'],
        capture_output=True, text=True)
    if not png.exists():
        print(f'RENDER FAILED {tag}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}')
        sys.exit(1)
    return np.asarray(Image.open(png).convert('RGB'), np.float32)


# as-shot for this scene is around (2.0, 1.0, 1.6); A ~ that, B clearly warmer
def cc_blob(ill, temp):
    raw = (struct.pack('<4f', 1, 0, 0, 0) + struct.pack('<4f', 0, 1, 0, 0)
           + struct.pack('<4f', 0, 0, 1, 0) + struct.pack('<4f', 0, 0, 0, 0) * 3
           + struct.pack('<6i', 0, 0, 0, 0, 0, 0)
           + struct.pack('<4i', ill, 2, 4, 1)
           + struct.pack('<4f', 0.333, 0.333, temp, 1.0)
           + struct.pack('<2i', 1, 2))
    return enc(raw)


a = render('ccCam', cc_blob(10, 5003.0))   # as shot in camera
b = render('ccWarm', cc_blob(2, 3500.0))   # daylight 3500K = warm
diff = np.abs(a - b)
print(f'mean |A-B| = {diff.mean():.2f} 8-bit steps, p99 = {np.percentile(diff, 99):.1f}')
print(f'per-channel mean diff: R {diff[..., 0].mean():.2f}  '
      f'G {diff[..., 1].mean():.2f}  B {diff[..., 2].mean():.2f}')
print('PASS: WB change alters the DCP render' if diff.mean() > 1.0
      else 'FAIL: renders are (near) identical — WB still ignored')
