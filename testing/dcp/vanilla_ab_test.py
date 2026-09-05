#!/usr/bin/env python3
"""Patched vs vanilla darktable on the NORMAL pipeline (no DCP anywhere):
default processing (standard input matrix, WB, color calibration, sigmoid)
must be pixel-identical between the two builds."""
import subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image

PATCHED = Path.home() / 'Documents/darktable-dcp/build/bin/darktable-cli'
VANILLA = Path('/tmp/vanilla-dt/build/bin/darktable-cli')
IMGDIR = Path.home() / 'Documents/dcp2icc/testing/Canon EOS RP'
OUT = Path(__file__).parent / 'renders' / 'vanilla_ab'
OUT.mkdir(parents=True, exist_ok=True)
IMAGES = ['IMG_8736', 'IMG_8919', 'IMG_9029', 'IMG_9399', '19-43-22-103']


def render(binary, tag, name):
    png = OUT / f'{name}-{tag}.png'
    png.unlink(missing_ok=True)
    r = subprocess.run(
        [str(binary), str(IMGDIR / f'{name}.CR3'), str(png), '--width', '1200',
         '--core', '--disable-opencl', '--configdir', str(OUT / f'cfg-{tag}'),
         '--library', ':memory:',
         '--conf', 'write_sidecar_files=never',
         '--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
         '--conf', 'plugins/darkroom/chromatic-adaptation=modern'],
        capture_output=True, text=True)
    if not png.exists():
        print(f'RENDER FAILED {name} [{tag}]\n{r.stdout[-1200:]}\n{r.stderr[-1200:]}')
        sys.exit(1)
    return np.asarray(Image.open(png).convert('RGB'), np.int16)


worst = 0.0
for name in IMAGES:
    a = render(PATCHED, 'patched', name)
    b = render(VANILLA, 'vanilla', name)
    if a.shape != b.shape:
        print(f'{name}: SIZE MISMATCH {a.shape} vs {b.shape}')
        sys.exit(1)
    diff = np.abs(a - b)
    frac = float((diff > 0).mean())
    print(f'{name:14s} max diff {diff.max():3d}/255  mean {diff.mean():.5f}  '
          f'pixels differing {frac * 100:.3f}%')
    worst = max(worst, float(diff.max()))
print('PASS: non-DCP pipeline identical' if worst <= 1
      else f'FAIL: non-DCP pipeline differs (max {worst:.0f}/255)')
