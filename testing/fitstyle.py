#!/usr/bin/env python3
"""Fit a "camera colors" darktable style from native modules against the
Lightroom (Camera Standard) references — resumable, with progress output.

The fit runs entirely in the pinned Docker reference image:

    docker run --rm --entrypoint /env/bin/python3 \
        -v /path/to/repo:/work camicc-testing \
        /work/testing/fitstyle.py --workdir "/work/testing/Canon EOS RP/fit"

Stop it at any point (Ctrl-C / kill); re-running the same command resumes:
every darktable render is cached on disk keyed by its exact history stack,
so the deterministic optimization replays through the cache in seconds and
continues where it stopped.

Module stack fitted (the user's modern-workflow defaults stay untouched:
white balance = camera reference, color calibration = as shot):

    exposure            global EV
    colorequal #1       per-hue hue/sat/brightness nodes (whole range)
    colorequal #2       ditto, parametric-masked to scene highlights
                        (the DCP LookTable is value-dependent: orange hue
                        rotation grows +17deg->+33deg with V, lavender
                        +4deg->+25deg -- one instance cannot express that)
    colorbalancergb     luminance-zone saturation (shadows/mid/highlights)
    agx                 tone curve + primaries (inset/rotation/outset --
                        the global, matrix-like part of the look)
    sigmoid             explicitly disabled (agx is the tone mapper)

Fitting method, per stage:
  tone + primaries    coordinate descent (probe +/- step, shrink)
  colorequal          closed loop through real renders: measure per-hue-bin
                      residuals vs the reference, solve node updates with a
                      MEASURED response Jacobian (probe each node once),
                      damped; repeat
  zones               closed loop on per-luminance-zone chroma residuals

Objective: mean Lab dE76 over the central 80% of each image, averaged over
all reference images (IMG_9399 weighted double -- portrait priority).

The run is budgeted (default 55 min): when the budget runs out the fit stops
gracefully, saves state and emits the outputs. Progress lines report the
convergence pace so a stalling run can be stopped early by hand -- the
current-best style + presets are (re)written to <workdir>/out after every
stage, and `--emit-only` regenerates them from the saved state at any time.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtxmp  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

IMAGES = ['IMG_8736', 'IMG_8919', 'IMG_9029', 'IMG_9399', '19-43-22-103']
WEIGHTS = {'IMG_9399': 2.0}
FIT_SIZE = 540
FINAL_SIZE = 1200
DAMP = 0.65
HUES = ['red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'lavender',
        'magenta']
# Lab-hue bin centers used to measure residuals (bin width 45 deg). The
# colorequal node responses are measured against these bins (Jacobian), so
# the centers only need to be a stable, roughly-uniform hue partition.
BIN_CENTERS = [30, 65, 100, 140, 200, 265, 310, 350]
MODERN_CONF = ('--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
               '--conf', 'plugins/darkroom/chromatic-adaptation=modern')

# --------------------------------------------------------------- packers

def enc(raw: bytes) -> str:
    comp = zlib.compress(raw, 9)
    factor = max(1, math.ceil(len(raw) / len(comp)))
    return 'gz%02d' % factor + base64.b64encode(comp).decode()


def colorequal_params(sat, hue, bright, smoothing=1.0):
    """dt_iop_colorequal_params_t v4 (darktable 5.4)."""
    return enc(struct.pack('<6fi24ff', 0.1, smoothing, 0.0, 1.0, 1.5, 1.0, 1,
                           *sat, *hue, *bright, 0.0))


def colorbalancergb_params(sat_shadows=0.0, sat_midtones=0.0,
                           sat_highlights=0.0, sat_global=0.0):
    """dt_iop_colorbalancergb_params_t v5, saturation-only use."""
    v = [0.0] * 12 + [1.0, 0.0, 1.0] + [0.0, 0.0, 0.0, 0.0]
    v += [sat_global, sat_highlights, sat_midtones, sat_shadows]
    v += [0.0] + [0.0] * 4 + [0.1845, 0.0, 0.1845, 0.0]
    return enc(struct.pack('<32fi', *v, 1))


AGX_KEYS = ['look_lift', 'look_slope', 'look_brightness', 'look_saturation',
            'look_hue_mix', 'range_black_ev', 'range_white_ev', 'dr_scaling',
            'pivot_x', 'pivot_y', 'contrast', 'lin_below', 'lin_above',
            'toe_power', 'shoulder_power', 'gamma', 'auto_gamma',
            'black_ratio', 'white_ratio', 'base_primaries', 'disable_prim',
            'red_inset', 'red_rot', 'green_inset', 'green_rot', 'blue_inset',
            'blue_rot', 'master_outset', 'master_unrot', 'red_outset',
            'red_unrot', 'green_outset', 'green_unrot', 'blue_outset',
            'blue_unrot', 'reverse_all']
AGX_DEFAULTS = dict(
    look_lift=0.0, look_slope=1.0, look_brightness=1.0, look_saturation=1.0,
    look_hue_mix=0.0, range_black_ev=-10.0, range_white_ev=6.5,
    dr_scaling=0.1, pivot_x=0.606060606061, pivot_y=0.18, contrast=2.4,
    lin_below=0.0, lin_above=0.0, toe_power=1.5, shoulder_power=1.5,
    gamma=2.2, auto_gamma=0, black_ratio=0.0, white_ratio=1.0,
    base_primaries=2, disable_prim=0,
    # default AgX primaries (Blender/Sobotka insets)
    red_inset=0.29462, red_rot=0.03540, green_inset=0.25862,
    green_rot=-0.02109, blue_inset=0.14641, blue_rot=-0.06306,
    master_outset=1.0, master_unrot=0.0, red_outset=0.29078,
    red_unrot=0.03540, green_outset=0.26316, green_unrot=-0.02109,
    blue_outset=0.04581, blue_unrot=-0.06306, reverse_all=0)


def agx_params(**kw):
    """dt_iop_agx_params_t v7 (darktable 5.4)."""
    p = dict(AGX_DEFAULTS)
    p.update(kw)
    return enc(struct.pack('<16fi2f2i14fi', *[p[k] for k in AGX_KEYS]))


def exposure_params(ev):
    raw = struct.pack('<iffff', 0, -0.000244140625, ev, 50.0, -4.0)
    return raw.hex() + '0100000001000000'


def blend_mask_gray(lo0, lo1, hi0=1.0, hi1=1.0):
    """develop_blend_params v14: parametric mask on the scene-referred
    gray (luminance) input channel, thresholds in scene-linear units.
    A pair >= 1.0 is an open (unbounded) end."""
    raw = bytearray(zlib.decompress(base64.b64decode(dtxmp.BLEND_DEFAULT[4:])))
    struct.pack_into('<i', raw, 0, 5)           # ENABLED | CONDITIONAL
    struct.pack_into('<i', raw, 4, 4)           # blend_cst = RGB_SCENE
    struct.pack_into('<i', raw, 28, 1 << 0)     # blendif: GRAY_in
    struct.pack_into('<4f', raw, 68, lo0, lo1, hi0, hi1)
    return enc(bytes(raw))


HIGHLIGHT_MASK = (0.10, 0.25)   # scene-linear luminance ramp for colorequal#2

# ------------------------------------------------------------ parameters

def default_params():
    """Starting point = the committed native style (v1)."""
    return {
        'exposure_ev': 0.5875,
        'ce1': {'sat': [1.023, 0.995, 0.962, 0.963, 0.999, 0.997, 0.976,
                        1.001],
                'hue': [-5.61, -4.94, 2.12, 6.05, -6.96, -10.71, -9.23,
                        -1.76],
                'bright': [1.012, 0.991, 0.989, 0.988, 1.042, 1.062, 1.076,
                           1.053]},
        'ce2': {'sat': [1.0] * 8, 'hue': [0.0] * 8, 'bright': [1.0] * 8},
        'cb': {'sat_shadows': 0.075, 'sat_midtones': 0.0,
               'sat_highlights': 0.15, 'sat_global': 0.0},
        'agx': {'contrast': 3.6, 'pivot_y': 0.15, 'toe_power': 1.5,
                'shoulder_power': 3.3, 'look_saturation': 0.925,
                'look_hue_mix': 0.6,
                'red_inset': AGX_DEFAULTS['red_inset'],
                'red_rot': AGX_DEFAULTS['red_rot'],
                'green_inset': AGX_DEFAULTS['green_inset'],
                'green_rot': AGX_DEFAULTS['green_rot'],
                'blue_inset': AGX_DEFAULTS['blue_inset'],
                'blue_rot': AGX_DEFAULTS['blue_rot'],
                'red_outset': AGX_DEFAULTS['red_outset'],
                'green_outset': AGX_DEFAULTS['green_outset'],
                'blue_outset': AGX_DEFAULTS['blue_outset']},
    }


def ops_for(p):
    """History stack for a param set. Everything not listed here keeps its
    modern-workflow default (white balance camera reference, color
    calibration as shot via the explicit entry below, matching the GUI)."""
    ce2_active = (any(abs(h) > 1e-4 for h in p['ce2']['hue'])
                  or any(abs(s - 1) > 1e-4 for s in p['ce2']['sat'])
                  or any(abs(b - 1) > 1e-4 for b in p['ce2']['bright']))
    ops = [
        ('colorin', 1, 7, dtxmp.COLORIN_STANDARD_MATRIX, None, 0, ''),
        ('channelmixerrgb', 1, 3, dtxmp.CHMIX_PARAMS, None, 0, ''),
        ('exposure', 1, 7, exposure_params(p['exposure_ev']), None, 0, ''),
        ('colorequal', 1, 4,
         colorequal_params(**p['ce1']), None, 0, ''),
        ('colorequal', 1 if ce2_active else 0, 4,
         colorequal_params(**p['ce2']),
         blend_mask_gray(*HIGHLIGHT_MASK), 1, 'highlights'),
        ('colorbalancergb', 1, 5, colorbalancergb_params(**p['cb']),
         None, 0, ''),
        ('sigmoid', 0, 3, dtxmp.TONEMAPPERS['sigmoid'][1], None, 0, ''),
        ('agx', 1, 7, agx_params(**p['agx']), None, 0, ''),
        ('lens', 1, dtxmp.LENS_VERSION, dtxmp.LENS_PARAMS, None, 0, ''),
    ]
    return ops


def make_xmp(raw_name, out_path, ops):
    items = []
    for i, (op, en, ver, params, blend, prio, name) in enumerate(ops):
        items.append(f'''     <rdf:li
      darktable:num="{i}"
      darktable:operation="{op}"
      darktable:enabled="{en}"
      darktable:modversion="{ver}"
      darktable:params="{params}"
      darktable:multi_name="{name}"
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="{prio}"
      darktable:blendop_version="14"
      darktable:blendop_params="{blend or dtxmp.BLEND_DEFAULT}"/>''')
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

# ------------------------------------------------------------- rendering

class Renderer:
    """Content-addressed, disk-cached darktable-cli renders (the resume
    mechanism: identical history + image + size = cache hit)."""

    def __init__(self, workdir: Path, imgdir: Path):
        self.cache = workdir / 'cache'
        self.tmp = workdir / 'tmp'
        self.imgdir = imgdir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def key(self, raw, ops, size):
        blob = json.dumps([raw, size, [list(map(str, o)) for o in ops]])
        return hashlib.md5(blob.encode()).hexdigest()

    def render(self, raw, ops, size):
        out = self.cache / f'{self.key(raw, ops, size)}.png'
        if out.exists():
            self.hits += 1
            return out
        self.misses += 1
        tag = out.stem
        xmp = self.tmp / f'{tag}.xmp'
        make_xmp(f'{raw}.CR3', xmp, ops)
        part = self.tmp / f'{tag}.png'
        part.unlink(missing_ok=True)
        cmd = ['darktable-cli', str(self.imgdir / f'{raw}.CR3'), str(xmp),
               str(part), '--width', str(size), '--height', str(size),
               '--core', '--configdir', str(self.tmp / f'cfg{tag[:8]}'),
               '--library', ':memory:',
               '--conf', 'write_sidecar_files=never'] + list(MODERN_CONF)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if not part.exists():
            sys.exit(f'darktable-cli failed for {raw}:\n{r.stdout[-2000:]}'
                     f'\n{r.stderr[-2000:]}')
        part.replace(out)          # atomic: a killed render never caches
        return out

    def batch(self, ops, size, images=IMAGES):
        with ThreadPoolExecutor(max_workers=4) as ex:
            return dict(zip(images, ex.map(
                lambda r: self.render(r, ops, size), images)))

# --------------------------------------------------------------- metric

def srgb_to_lab(a):
    a = a / 255.0
    a = np.where(a > 0.04045, ((a + 0.055) / 1.055) ** 2.4, a / 12.92)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = a @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


class RefSet:
    def __init__(self, imgdir: Path, size: int):
        self.lab = {}
        self.size = size
        for name in IMAGES:
            im = ImageOps.exif_transpose(
                Image.open(imgdir / f'lightroom_{name}.jpg')).convert('RGB')
            s = size / max(im.size)
            im = im.resize((round(im.width * s), round(im.height * s)),
                           Image.LANCZOS)
            self.lab[name] = srgb_to_lab(np.asarray(im, float))

    def measure(self, name, png):
        """Full residual measurement of one render vs its reference."""
        lb = self.lab[name]
        im = ImageOps.exif_transpose(Image.open(png)).convert('RGB')
        if im.size != (lb.shape[1], lb.shape[0]):
            im = im.resize((lb.shape[1], lb.shape[0]), Image.LANCZOS)
        la = srgb_to_lab(np.asarray(im, float))
        h, w = la.shape[:2]
        dy, dx = round(h * 0.1), round(w * 0.1)
        la, lb = la[dy:h - dy, dx:w - dx], lb[dy:h - dy, dx:w - dx]
        dE = np.sqrt(((la - lb) ** 2).sum(-1))
        Ca = np.hypot(la[..., 1], la[..., 2])
        Cb = np.hypot(lb[..., 1], lb[..., 2])
        Ha = np.degrees(np.arctan2(la[..., 2], la[..., 1])) % 360
        Hb = np.degrees(np.arctan2(lb[..., 2], lb[..., 1])) % 360
        La, Lb = la[..., 0], lb[..., 0]
        m = {'de': float(dE.mean()), 'dL': float((La - Lb).mean()),
             'absL': float(np.abs(La - Lb).mean()),
             'bins': [], 'hi_bins': [], 'zones': []}
        chroma = Cb > 10
        bright = Lb > 65
        for hc in BIN_CENTERS:
            sel = (np.abs((Hb - hc + 180) % 360 - 180) < 22.5) & chroma
            m['bins'].append(_bin_stats(sel, Ha, Hb, Ca, Cb, La, Lb))
            m['hi_bins'].append(_bin_stats(sel & bright, Ha, Hb, Ca, Cb,
                                           La, Lb))
        for zsel in [Lb < 35, (Lb >= 35) & (Lb < 65), Lb >= 65]:
            sel = zsel & chroma
            n = int(sel.sum())
            m['zones'].append(
                [float(np.median(Ca[sel] / np.maximum(Cb[sel], 1e-6)))
                 if n > 200 else 1.0, n])
        return m


def _bin_stats(sel, Ha, Hb, Ca, Cb, La, Lb):
    n = int(sel.sum())
    if n < 200:
        return [0.0, 1.0, 1.0, n]
    dh = float(np.median(((Ha[sel] - Hb[sel]) + 180) % 360 - 180))
    cr = float(np.median(Ca[sel] / np.maximum(Cb[sel], 1e-6)))
    lr = float(np.median((La[sel] + 5) / (Lb[sel] + 5)))
    return [dh, cr, lr, n]


def combine(measures):
    """Weighted objective + aggregated residual vectors over all images."""
    wsum = sum(WEIGHTS.get(n, 1.0) for n in measures)
    obj = sum(WEIGHTS.get(n, 1.0) * m['de']
              for n, m in measures.items()) / wsum

    def agg(key):
        out = []
        for k in range(8):
            dh = cr = lr = wtot = 0.0
            for n, m in measures.items():
                d, c, r, cnt = m[key][k]
                w = WEIGHTS.get(n, 1.0) * min(cnt, 20000)
                dh += w * d
                cr += w * math.log(max(c, 1e-3))
                lr += w * math.log(max(r, 1e-3))
                wtot += w
            out.append((dh / wtot, math.exp(cr / wtot), math.exp(lr / wtot))
                       if wtot > 0 else (0.0, 1.0, 1.0))
        return out

    zones = []
    for z in range(3):
        v = wtot = 0.0
        for n, m in measures.items():
            r, cnt = m['zones'][z]
            w = WEIGHTS.get(n, 1.0) * min(cnt, 20000)
            v += w * math.log(max(r, 1e-3))
            wtot += w
        zones.append(math.exp(v / wtot) if wtot > 0 else 1.0)
    return obj, agg('bins'), agg('hi_bins'), zones

# --------------------------------------------------------- style output

STYLE_NAME = 'Canon EOS RP camera colors (native modules)'
PRESET_TAIL = '''  <autoapply>0</autoapply>
  <model>%</model>
  <maker>%</maker>
  <lens>%</lens>
  <iso_min>0.000000</iso_min>
  <iso_max>340282346638528859811704183484516925440.000000</iso_max>
  <exposure_min>0.000000</exposure_min>
  <exposure_max>340282346638528859811704183484516925440.000000</exposure_max>
  <aperture_min>0.000000</aperture_min>
  <aperture_max>340282346638528859811704183484516925440.000000</aperture_max>
  <focal_length_min>0</focal_length_min>
  <focal_length_max>1000</focal_length_max>'''


def style_modules(p):
    """(operation, version, params, blendop, multi_priority, multi_name,
    enabled) for every module the style/presets ship."""
    ce2_active = (any(abs(h) > 1e-4 for h in p['ce2']['hue'])
                  or any(abs(s - 1) > 1e-4 for s in p['ce2']['sat'])
                  or any(abs(b - 1) > 1e-4 for b in p['ce2']['bright']))
    mods = [
        ('exposure', 7, exposure_params(p['exposure_ev']), None, 0, '', 1),
        ('colorequal', 4, colorequal_params(**p['ce1']), None, 0, '', 1),
        ('colorbalancergb', 5, colorbalancergb_params(**p['cb']),
         None, 0, '', 1),
        ('sigmoid', 3, dtxmp.TONEMAPPERS['sigmoid'][1], None, 0, '', 0),
        ('agx', 7, agx_params(**p['agx']), None, 0, '', 1),
    ]
    if ce2_active:
        mods.insert(2, ('colorequal', 4, colorequal_params(**p['ce2']),
                        blend_mask_gray(*HIGHLIGHT_MASK), 1, 'highlights', 1))
    return mods


def emit_outputs(workdir: Path, p, obj=None):
    """Write the current-best .dtstyle + per-module .dtpresets (+ a params
    summary) -- callable at any point of the fit."""
    out = workdir / 'out'
    out.mkdir(parents=True, exist_ok=True)
    mods = style_modules(p)
    score_note = f' (fit dE {obj:.2f})' if obj is not None else ''
    plugins = []
    for i, (op, ver, params, blend, prio, name, en) in enumerate(mods):
        plugins.append(f'''  <plugin>
   <num>{i}</num>
   <module>{ver}</module>
   <operation>{op}</operation>
   <op_params>{params}</op_params>
   <enabled>{en}</enabled>
   <blendop_params>{blend or dtxmp.BLEND_DEFAULT}</blendop_params>
   <blendop_version>14</blendop_version>
   <multi_priority>{prio}</multi_priority>
   <multi_name>{name}</multi_name>
   <multi_name_hand_edited>0</multi_name_hand_edited>
  </plugin>''')
    body = '\n'.join(plugins)
    (out / f'{STYLE_NAME}.dtstyle').write_text(
        f'''<?xml version="1.0" encoding="UTF-8"?>
<darktable_style version="1.0">
 <info>
  <name>{STYLE_NAME}</name>
  <description>Approximates the Canon EOS RP 'Camera Standard' colors with stock scene-referred modules, fitted against Lightroom references under the default (modern) workflow{score_note}. See the guide.</description>
 </info>
 <style>
{body}
 </style>
</darktable_style>
''')
    for op, ver, params, blend, prio, name, en in mods:
        if op == 'sigmoid':
            continue
        suffix = f' {name}' if name else ''
        (out / f'{op} - EOS RP camera colors{suffix}.dtpreset').write_text(
            f'''<?xml version="1.0" encoding="UTF-8"?>
<darktable_preset version="1.0">
 <preset>
  <name>EOS RP camera colors{suffix}</name>
  <description>Part of: {STYLE_NAME}</description>
  <operation>{op}</operation>
  <op_params>{params}</op_params>
  <op_version>{ver}</op_version>
  <enabled>{en}</enabled>
{PRESET_TAIL}
  <blendop_params>{blend or dtxmp.BLEND_DEFAULT}</blendop_params>
  <blendop_version>14</blendop_version>
  <multi_priority>{prio}</multi_priority>
  <multi_name>{name}</multi_name>
  <multi_name_hand_edited>0</multi_name_hand_edited>
  <filter>0</filter>
  <def>0</def>
  <format>15</format>
 </preset>
</darktable_preset>
''')
    (out / 'params.json').write_text(json.dumps(p, indent=1))
    (out / 'GUI-values.md').write_text(gui_values(p, obj))
    print(f'[emit] style + presets written to {out}', flush=True)


def gui_values(p, obj=None):
    """Markdown table of GUI slider values for the manual-setup guide."""
    a = p['agx']
    deg = math.degrees
    lines = ['# GUI slider values (generated by fitstyle.py)', '']
    if obj is not None:
        lines += [f'Fit objective (weighted dE76 vs Lightroom): {obj:.2f}',
                  '']
    lines += [f"## exposure: **{p['exposure_ev']:+.2f} EV**", '']
    for inst, title in [('ce1', 'color equalizer (instance 1, whole range)'),
                        ('ce2', 'color equalizer (instance 2, "highlights", '
                                'parametric mask: input gray '
                                f'{HIGHLIGHT_MASK[0]:.2f} -> '
                                f'{HIGHLIGHT_MASK[1]:.2f})')]:
        g = p[inst]
        if inst == 'ce2' and all(abs(h) < 1e-4 for h in g['hue']) \
                and all(abs(s - 1) < 1e-4 for s in g['sat']):
            lines += [f'## {title}: not used', '']
            continue
        lines += [f'## {title}', '',
                  '| node | hue | saturation | brightness |', '|---|---|---|---|']
        for i, n in enumerate(HUES):
            lines.append(f"| {n} | {g['hue'][i]:+.1f}° | "
                         f"{100 * (g['sat'][i] - 1):+.1f} % | "
                         f"{100 * (g['bright'][i] - 1):+.1f} % |")
        lines.append('')
    cb = p['cb']
    lines += ['## color balance rgb (4 ways tab, saturation column)', '',
              f"- shadows: **{100 * cb['sat_shadows']:+.0f} %**",
              f"- mid-tones: **{100 * cb['sat_midtones']:+.0f} %**",
              f"- highlights: **{100 * cb['sat_highlights']:+.0f} %**",
              f"- global (master tab): **{100 * cb['sat_global']:+.0f} %**",
              '']
    lines += ['## agx', '',
              f"- curve > contrast: **{a['contrast']:.2f}**",
              f"- curve > pivot target output: **{a['pivot_y']:.3f}**",
              f"- curve > toe power: **{a['toe_power']:.2f}**",
              f"- curve > shoulder power: **{a['shoulder_power']:.2f}**",
              f"- look > saturation: **{a['look_saturation']:.2f}**",
              f"- look > preserve hue: **{a['look_hue_mix']:.2f}**",
              '', 'primaries tab (attenuation / rotation):', '']
    for c in ['red', 'green', 'blue']:
        lines.append(f"- {c}: attenuation **{100 * a[f'{c}_inset']:.1f} %**, "
                     f"rotation **{deg(a[f'{c}_rot']):+.1f}°**, "
                     f"purity boost **{100 * a[f'{c}_outset']:.1f} %**")
    lines += ['', '(sigmoid, filmic rgb and base curve stay off; white '
              'balance and color calibration keep their defaults)', '']
    return '\n'.join(lines)


class StopFit(Exception):
    pass


# ------------------------------------------------------------ optimizer

class Fitter:
    def __init__(self, workdir: Path, imgdir: Path, size=FIT_SIZE,
                 budget_min=55.0):
        self.workdir = workdir
        self.rend = Renderer(workdir, imgdir)
        self.refs = RefSet(imgdir, size)
        self.size = size
        self.t0 = time.time()
        self.budget_s = budget_min * 60.0
        self.state_file = workdir / 'state.json'
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())
            print(f'[resume] loaded state: best objective '
                  f"{self.state['best_obj']:.3f}", flush=True)
        else:
            self.state = {'params': default_params(), 'best_obj': None,
                          'start_obj': None, 'jacobian': None, 'log': [],
                          'history': [], 'stage': None, 'stage_idx': 0,
                          'stages_total': 0, 'elapsed_s': 0}
        self.elapsed_base = self.state.get('elapsed_s', 0)
        self.evals = 0

    def save(self):
        self.state['elapsed_s'] = round(self.elapsed_base + self.elapsed())
        self.state_file.write_text(json.dumps(self.state, indent=1))

    def elapsed(self):
        return time.time() - self.t0

    def record(self, obj):
        """Track best-objective-over-time; used for the pace report."""
        self.state['history'].append(
            [self.evals, round(obj, 4), round(self.elapsed())])

    def pace(self, stage):
        h = self.state['history']
        best = self.state['best_obj']
        start = self.state['start_obj']
        recent = [x for x in h if x[0] >= self.evals - 20]
        d_recent = (recent[0][1] - best) if len(recent) > 1 else 0.0
        left = max(0.0, self.budget_s - self.elapsed())
        idx, tot = self.state['stage_idx'], self.state['stages_total']
        pct = 100.0 * idx / max(tot, 1)
        eta = (self.elapsed() / max(idx, 1)) * (tot - idx)
        print(f'[pace] {idx}/{tot} stages ({pct:.0f}%): objective '
              f'{best:.3f} (start {start:.3f}, total -{start - best:.3f}, '
              f'last ~20 evals -{d_recent:.3f})  '
              f'elapsed {self.elapsed() / 60:.0f}m, ETA '
              f'{min(eta, left) / 60:.0f}m, budget left {left / 60:.0f}m',
              flush=True)

    def evaluate(self, p, note=''):
        if self.elapsed() > self.budget_s:
            raise StopFit
        t0 = time.time()
        pngs = self.rend.batch(ops_for(p), self.size)
        meas = {n: self.refs.measure(n, f) for n, f in pngs.items()}
        obj, bins, hi_bins, zones = combine(meas)
        self.evals += 1
        if self.state['start_obj'] is None:
            self.state['start_obj'] = obj
        per = '  '.join(f"{n.split('_')[-1]} {m['de']:.2f}"
                        for n, m in meas.items())
        print(f'  eval#{self.evals:<4d} {obj:6.3f}  [{per}]  '
              f'{time.time() - t0:5.1f}s  {note}', flush=True)
        return obj, bins, hi_bins, zones, meas

    # ---- generic coordinate descent on scalar params ----
    def coordinate_stage(self, stage, spec, rounds=2):
        p = self.state['params']
        best, *_ = self.evaluate(p, f'{stage} start')
        if self.state['best_obj'] is None or best < self.state['best_obj']:
            self.state['best_obj'] = best
        for rnd in range(rounds):
            improved = False
            for (group, key), step, lo, hi in spec:
                base_v = p[group][key] if group else p[key]
                for delta in (step, -step):
                    v = min(hi, max(lo, base_v + delta))
                    if abs(v - base_v) < 1e-6:
                        continue
                    q = json.loads(json.dumps(p))
                    if group:
                        q[group][key] = v
                    else:
                        q[key] = v
                    obj, *_ = self.evaluate(
                        q, f'{stage} {group or ""}.{key}={v:.4g}')
                    if obj < best - 1e-4:
                        best, p = obj, q
                        self.state['params'] = p
                        self.state['best_obj'] = best
                        self.state['log'].append(
                            [stage, f'{group}.{key}', v, best])
                        self.record(best)
                        self.save()
                        improved = True
                        break
            spec = [((g, k), s * 0.5, lo, hi)
                    for (g, k), s, lo, hi in spec]
            print(f'[{stage}] round {rnd + 1}: objective {best:.3f}',
                  flush=True)
            if not improved:
                break
        return best

    # ---- measured colorequal response Jacobian ----
    def jacobian(self):
        if self.state['jacobian'] is not None:
            return np.array(self.state['jacobian'])
        print('[jacobian] measuring colorequal node responses '
              '(one probe render per node)...', flush=True)
        p = self.state['params']
        _, bins0, _, _, _ = self.evaluate(p, 'jacobian baseline')
        probe_deg = 12.0
        J = np.zeros((8, 8))
        for node in range(8):
            q = json.loads(json.dumps(p))
            q['ce1']['hue'] = list(q['ce1']['hue'])
            q['ce1']['hue'][node] += probe_deg
            _, bins, _, _, _ = self.evaluate(q, f'jacobian probe {HUES[node]}')
            for b in range(8):
                J[b, node] = (bins[b][0] - bins0[b][0]) / probe_deg
        self.state['jacobian'] = J.tolist()
        self.save()
        print('[jacobian] response matrix (rows=Lab bins, cols=nodes):',
              flush=True)
        for b in range(8):
            print('   ', ' '.join(f'{J[b, n]:+5.2f}' for n in range(8)),
                  flush=True)
        return J

    # ---- closed-loop colorequal fitting ----
    def ceq_stage(self, instance, iters=3):
        """instance: 'ce1' (residuals over all pixels) or 'ce2'
        (highlight-masked instance, residuals over bright pixels)."""
        J = self.jacobian()
        Jinv = np.linalg.pinv(J, rcond=0.1)
        p = self.state['params']
        best, bins, hi_bins, zones, _ = self.evaluate(p, f'{instance} start')
        if self.state['best_obj'] is None or best < self.state['best_obj']:
            self.state['best_obj'] = best
        for it in range(iters):
            res = hi_bins if instance == 'ce2' else bins
            dh = np.array([r[0] for r in res])          # render - ref
            cr = np.array([r[1] for r in res])
            lr = np.array([r[2] for r in res])
            upd_h = -DAMP * (Jinv @ dh)
            # sat/bright respond ~proportionally at the matching node: use
            # the same bin->node assignment via the Jacobian pseudo-inverse
            upd_s = np.exp(-DAMP * (Jinv @ np.log(np.clip(cr, 0.5, 2.0))))
            upd_b = np.exp(-0.5 * DAMP * (Jinv @ np.log(np.clip(lr, 0.7,
                                                                1.4))))
            q = json.loads(json.dumps(p))
            g = q[instance]
            g['hue'] = [max(-40, min(40, h + u))
                        for h, u in zip(g['hue'], upd_h)]
            g['sat'] = [max(0.3, min(1.9, s * u))
                        for s, u in zip(g['sat'], upd_s)]
            g['bright'] = [max(0.5, min(1.5, b * u))
                           for b, u in zip(g['bright'], upd_b)]
            obj, bins, hi_bins, zones, _ = self.evaluate(
                q, f'{instance} iter {it + 1}')
            if obj < best - 1e-4:
                best, p = obj, q
                self.state['params'] = p
                self.state['best_obj'] = best
                self.state['log'].append([instance, f'iter{it}', 0, best])
                self.record(best)
                self.save()
            else:
                print(f'[{instance}] no further improvement', flush=True)
                break
        return best

    # ---- luminance-zone saturation ----
    def zones_stage(self, iters=2):
        p = self.state['params']
        best, _, _, zones, _ = self.evaluate(p, 'zones start')
        keys = ['sat_shadows', 'sat_midtones', 'sat_highlights']
        for it in range(iters):
            q = json.loads(json.dumps(p))
            for k, ratio in zip(keys, zones):
                # chroma ratio >1 = too saturated in that zone
                q['cb'][k] = max(-0.5, min(0.5,
                                           q['cb'][k] - DAMP * 0.5
                                           * math.log(max(ratio, 1e-3))))
            obj, _, _, zones, _ = self.evaluate(q, f'zones iter {it + 1}')
            if obj < best - 1e-4:
                best, p = obj, q
                self.state['params'] = p
                self.state['best_obj'] = best
                self.record(best)
                self.save()
            else:
                print('[zones] no further improvement', flush=True)
                break
        return best

    def run(self, rounds=2, only=None):
        tone_spec = [
            (('', 'exposure_ev'), 0.15, -1.0, 2.0),
            (('agx', 'contrast'), 0.3, 1.0, 6.0),
            (('agx', 'pivot_y'), 0.02, 0.05, 0.3),
            (('agx', 'toe_power'), 0.3, 0.5, 4.0),
            (('agx', 'shoulder_power'), 0.4, 0.5, 6.0),
            (('agx', 'look_saturation'), 0.05, 0.5, 1.5),
            (('agx', 'look_hue_mix'), 0.15, 0.0, 1.0),
        ]
        prim_spec = [
            (('agx', 'red_inset'), 0.05, 0.0, 0.6),
            (('agx', 'red_rot'), 0.03, -0.4, 0.4),
            (('agx', 'green_inset'), 0.05, 0.0, 0.6),
            (('agx', 'green_rot'), 0.03, -0.4, 0.4),
            (('agx', 'blue_inset'), 0.05, 0.0, 0.6),
            (('agx', 'blue_rot'), 0.03, -0.4, 0.4),
            (('agx', 'red_outset'), 0.05, 0.0, 0.6),
            (('agx', 'green_outset'), 0.05, 0.0, 0.6),
            (('agx', 'blue_outset'), 0.05, 0.0, 0.6),
        ]
        stages = []
        for rnd in range(rounds):
            stages += [
                (f'tone/{rnd + 1}',
                 lambda: self.coordinate_stage('tone', list(tone_spec))),
                (f'primaries/{rnd + 1}',
                 lambda: self.coordinate_stage('primaries', list(prim_spec))),
                (f'ce1/{rnd + 1}', lambda: self.ceq_stage('ce1')),
                (f'ce2/{rnd + 1}', lambda: self.ceq_stage('ce2', iters=2)),
                (f'zones/{rnd + 1}', lambda: self.zones_stage()),
            ]
        if only:
            # keep only the requested stages, in the requested order
            # (per round): --stages ce1,zones,tone
            stages = sorted(
                [(n, f) for n, f in stages if n.split('/')[0] in only],
                key=lambda s: (int(s[0].split('/')[1]),
                               only.index(s[0].split('/')[0])))
        self.state['stages_total'] = len(stages)
        # on resume all stages are re-entered; completed ones replay
        # instantly through the render cache
        try:
            for i, (name, fn) in enumerate(stages):
                print(f'===== stage {name} =====', flush=True)
                self.state['stage'] = name
                fn()
                self.state['stage_idx'] = i + 1
                self.save()
                self.pace(name)
                emit_outputs(self.workdir, self.state['params'],
                             self.state['best_obj'])
        except StopFit:
            print(f'[budget] time budget reached after '
                  f'{self.elapsed() / 60:.0f}m -- stopping; state saved, '
                  're-run to continue.', flush=True)
        emit_outputs(self.workdir, self.state['params'],
                     self.state['best_obj'])
        print(f'===== fit finished: objective {self.state["best_obj"]:.3f} '
              f'(cache {self.rend.hits} hits / {self.rend.misses} renders, '
              f'{self.elapsed() / 60:.0f}m) =====', flush=True)

# --------------------------------------------------------------- report

def final_report(workdir: Path, imgdir: Path):
    state = json.loads((workdir / 'state.json').read_text())
    p = state['params']
    rend = Renderer(workdir, imgdir)
    refs = RefSet(imgdir, FINAL_SIZE)
    print('[final] rendering at full validation size...', flush=True)
    pngs = rend.batch(ops_for(p), FINAL_SIZE)
    meas = {n: refs.measure(n, f) for n, f in pngs.items()}
    obj, *_ = combine(meas)

    lines = ['# fitstyle result', '',
             f'weighted objective (dE76): {obj:.3f}', '',
             '| image | dE76 mean |', '|---|---|']
    for n, m in meas.items():
        lines.append(f"| {n} | {m['de']:.2f} |")
    lines += ['', '## fitted parameters', '```json',
              json.dumps(p, indent=1), '```']
    (workdir / 'report.md').write_text('\n'.join(lines))

    # montage: reference | fitted render, one row per image
    tiles = []
    try:
        font = ImageFont.load_default(size=20)
    except TypeError:
        font = ImageFont.load_default()
    box = (560, 420)
    for n in IMAGES:
        for path, label in [(imgdir / f'lightroom_{n}.jpg',
                             f'{n}: Lightroom'),
                            (pngs[n], f"fitted style ({meas[n]['de']:.2f})")]:
            im = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
            im = ImageOps.pad(im, box, Image.LANCZOS, color=(30, 30, 30))
            lab = Image.new('RGB', (box[0], box[1] + 30), (16, 16, 16))
            lab.paste(im, (0, 30))
            ImageDraw.Draw(lab).text((8, 4), label, fill=(240, 240, 240),
                                     font=font)
            tiles.append(lab)
    w, h = tiles[0].size
    cols = 2
    rows = len(tiles) // cols
    out = Image.new('RGB', (w * cols + 24, h * rows + 8 * (rows + 1)),
                    (30, 30, 30))
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        out.paste(t, (8 + c * (w + 8), 8 + r * (h + 8)))
    out.save(workdir / 'montage.jpg', quality=90)
    print(f'[final] objective {obj:.3f}; report.md + montage.jpg written to '
          f'{workdir}', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--workdir', default=None,
                    help='fit state/cache directory '
                         '(default: <imgdir>/fit)')
    ap.add_argument('--imgdir',
                    default=str(Path(__file__).parent / 'Canon EOS RP'))
    ap.add_argument('--rounds', type=int, default=2)
    ap.add_argument('--budget-minutes', type=float, default=55.0,
                    help='wall-clock budget; the fit stops gracefully and '
                         'emits outputs when exceeded (default 55)')
    ap.add_argument('--stages', default=None,
                    help='comma-separated stage subset/order per round, '
                         'from: tone,primaries,ce1,ce2,zones')
    ap.add_argument('--weights', default=None,
                    help='per-image objective weights, e.g. '
                         '"IMG_8736=2,IMG_9029=2" (default: IMG_9399=2, '
                         'others 1). Changing weights resets the pace '
                         'tracking but keeps the fitted params and cache.')
    ap.add_argument('--report-only', action='store_true',
                    help='only (re)build report + montage from the state')
    ap.add_argument('--emit-only', action='store_true',
                    help='only (re)write style + presets from the state')
    ap.add_argument('--status', action='store_true',
                    help='print fit progress from the saved state and exit')
    ap.add_argument('--reset', action='store_true',
                    help='discard state.json (render cache is kept)')
    a = ap.parse_args()
    imgdir = Path(a.imgdir)
    workdir = Path(a.workdir) if a.workdir else imgdir / 'fit'
    workdir.mkdir(parents=True, exist_ok=True)
    if a.reset:
        (workdir / 'state.json').unlink(missing_ok=True)
        print('[reset] state discarded', flush=True)
    if a.status:
        sf = workdir / 'state.json'
        if not sf.exists():
            print('no fit state yet')
            return
        s = json.loads(sf.read_text())
        idx, tot = s.get('stage_idx', 0), s.get('stages_total', 0)
        pct = 100.0 * idx / max(tot, 1)
        el = s.get('elapsed_s', 0)
        eta = (el / max(idx, 1)) * (tot - idx) if idx else float('nan')
        start, best = s.get('start_obj'), s.get('best_obj')
        print(f'stage: {s.get("stage")} -- {idx}/{tot} done ({pct:.0f}%), '
              f'fit time {el / 60:.0f}m, ETA ~{eta / 60:.0f}m')
        if start is not None:
            gain = start - best
            print(f'distance to Lightroom (weighted dE76): '
                  f'{start:.3f} -> {best:.3f}  (-{gain:.3f}, '
                  f'{100 * gain / start:.1f}% closer; 0 = identical, '
                  f'~3 = the ICC-profile route)')
        for st, key, val, obj in s.get('log', [])[-5:]:
            print(f'  {st:12s} {key:22s} -> {obj:.3f}')
        return
    if a.emit_only:
        state = json.loads((workdir / 'state.json').read_text())
        emit_outputs(workdir, state['params'], state['best_obj'])
        return
    if a.weights:
        WEIGHTS.clear()
        for part in a.weights.split(','):
            k, v = part.split('=')
            WEIGHTS[k.strip()] = float(v)
        print(f'[weights] {WEIGHTS}', flush=True)
    if not a.report_only:
        fitter = Fitter(workdir, imgdir, budget_min=a.budget_minutes)
        if fitter.state.get('weights') != WEIGHTS:
            # objective changed meaning: keep params + cache, reset pace
            fitter.state.update({'weights': dict(WEIGHTS), 'start_obj': None,
                                 'best_obj': None, 'history': [],
                                 'stage_idx': 0, 'elapsed_s': 0})
            fitter.elapsed_base = 0
        try:
            fitter.run(rounds=a.rounds,
                       only=a.stages.split(',') if a.stages else None)
        except KeyboardInterrupt:
            print('\n[interrupted] state saved; re-run to resume, or use '
                  '--emit-only / --report-only for the current best.',
                  flush=True)
            emit_outputs(fitter.workdir, fitter.state['params'],
                         fitter.state['best_obj'])
            return
    final_report(workdir, imgdir)


if __name__ == '__main__':
    main()
