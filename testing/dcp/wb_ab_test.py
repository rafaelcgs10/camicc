#!/usr/bin/env python3
"""A/B test: with a DCP input profile, do different WB user coefficients
produce different renders? (Regression for the WB-as-illuminant control.)"""
import base64, math, struct, subprocess, sys, zlib
from pathlib import Path
import numpy as np
from PIL import Image

DT = Path.home() / 'Documents/darktable-dcp/build/bin/darktable-cli'
RAW = Path.home() / 'Documents/dcp2icc/testing/Canon EOS RP/IMG_8736.CR3'
DCP = (Path.home() / 'Documents/dcp2icc/dcps/Camera/Canon EOS RP/'
       'Canon EOS RP Camera Standard.dcp')
OUT = Path(__file__).parent / 'renders' / 'wb_ab'
OUT.mkdir(parents=True, exist_ok=True)


def enc(raw):
    c = zlib.compress(raw, 9)
    return 'gz%02d' % min(99, max(1, math.ceil(len(raw) / len(c)))) + \
        base64.b64encode(c).decode()


BLEND = 'gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU='


def write_xmp(path, wb):
    colorin = enc(struct.pack('<i', 27) + str(DCP).encode().ljust(512, b'\0')
                  + struct.pack('<iiii', 0, 0, 0, 4) + b'\0' * 512)
    # temperature v4: red, green, blue, various, preset (2 = user)
    temp = struct.pack('<ffffi', wb[0], wb[1], wb[2], float('nan'), 2).hex()
    ops = [('temperature', 1, 4, temp),
           ('colorin', 1, 7, colorin),
           ('channelmixerrgb', 0, 3,
            'gz04eJxjYGiwZ8AAxIqRD9iBmAmIWaDYbd8uO+sFh+30Zna7guxihMoDAKRhCIA=')]
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
a = render('wbA', (1.967041, 1.0, 1.616699))
b = render('wbB', (2.5, 1.0, 1.25))
diff = np.abs(a - b)
print(f'mean |A-B| = {diff.mean():.2f} 8-bit steps, p99 = {np.percentile(diff, 99):.1f}')
print(f'per-channel mean diff: R {diff[..., 0].mean():.2f}  '
      f'G {diff[..., 1].mean():.2f}  B {diff[..., 2].mean():.2f}')
print('PASS: WB change alters the DCP render' if diff.mean() > 1.0
      else 'FAIL: renders are (near) identical — WB still ignored')
