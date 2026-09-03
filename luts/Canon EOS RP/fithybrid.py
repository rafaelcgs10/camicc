#!/usr/bin/env python3
"""Hybrid interleaved fit: darktable tone parameters + Lightroom-match LUT.

Improvements over fitlut.py (kept as the simple fitter):

- SEGMENT anchors (segments.json): hand-placed homogeneous color patches
  compared by robust median — immune to the LR-vs-render crop/registration
  mismatch, and focused on the colors that matter (skin, sky, foliage...).
  They are both the fit OBJECTIVE and high-weight LUT training anchors.
- INTERLEAVED optimization: tone parameters (exposure, agx curve) and the
  synthetic-data calibration are probed round-robin (never exhausting one
  module before touching the next); after every probe the LUT is refit
  closed-form and the probe is judged on the TOTAL system.
- HYBRID LUT data: segment anchors (high weight) + dense edge-masked pixel
  pairs (coverage) + DCP-SYNTHETIC pairs (grid DNG rendered through the real
  darktable = measured F, camicc's Camera Standard pipeline = G) filling
  cells no image covers, at a calibrated weight.
- Objective = 0.6 * in-sample segment dE + 0.4 * leave-one-image-out segment
  dE (closed-form LUT refits), so generalization is optimized directly.
- Resumable (state.json + content-addressed render cache); emits the
  current-best .dtstyle / .dtpresets / .cube after every acceptance;
  --status / --budget-minutes / --emit-only / --report-only.

Run (uses the local native darktable-cli):
    python3 fithybrid.py --imgdir "testing/Canon EOS RP" \
        --workdir "testing/Canon EOS RP/fit-hybrid" --budget-minutes 60
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = None
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fitlut
from fitlut import (enc, srgb_lin, srgb_enc_, srgb_to_lab, exposure_params,
                    lens_params, COLORIN_STANDARD_MATRIX, CHMIX_PARAMS,
                    SIGMOID_OFF, BLEND_DEFAULT)

N = 33
PRIOR = 25.0
EDGE_W = 0.12
SEG_BOOST = 30000.0          # pixel-equivalent weight of one segment anchor
IMAGES = ['IMG_8736', 'IMG_8919', 'IMG_9029', 'IMG_9399', '19-43-22-103']
STYLE = 'Canon EOS RP Lightroom match (agx + LUT)'

# agx param vector indices in fitlut.AGX
IX_PIVOT_Y, IX_CONTRAST, IX_TOE, IX_SHOULDER = 9, 10, 13, 14


def default_params():
    return {
        'ev_global': 0.0,                      # offset on all aligned EVs
        'agx': {'contrast': 2.4, 'pivot_y': 0.18,
                'toe_power': 1.5, 'shoulder_power': 1.5},
        'syn': {'logw': -1.0, 'ev': 0.0, 'tint_b': 1.0},
        'evs': {},                             # per-image aligned EVs (pass 1)
    }


SPEC = [  # (path, step, lo, hi)  — round-robin order
    (('ev_global',), 0.15, -0.8, 0.8),
    (('agx', 'contrast'), 0.3, 1.2, 4.5),
    (('agx', 'pivot_y'), 0.02, 0.10, 0.30),
    (('agx', 'toe_power'), 0.25, 0.5, 3.5),
    (('agx', 'shoulder_power'), 0.3, 0.5, 4.0),
    (('syn', 'logw'), 0.5, -3.0, 1.5),
    (('syn', 'ev'), 0.15, -1.0, 1.0),
    (('syn', 'tint_b'), 0.04, 0.85, 1.20),
]


def get_p(p, path):
    for k in path[:-1]:
        p = p[k]
    return p[path[-1]]


def set_p(p, path, v):
    for k in path[:-1]:
        p = p[k]
    p[path[-1]] = v


# --------------------------------------------------------------- rendering
class R:
    def __init__(self, workdir: Path, imgdir: Path, syn_dng: Path):
        self.wd = workdir
        self.imgdir = imgdir
        self.syn_dng = syn_dng
        self.cache = workdir / 'cache'
        self.cache.mkdir(parents=True, exist_ok=True)

    def _agx_apply(self, agx):
        fitlut.AGX = list(fitlut.AGX)
        fitlut.AGX[IX_PIVOT_Y] = agx['pivot_y']
        fitlut.AGX[IX_CONTRAST] = agx['contrast']
        fitlut.AGX[IX_TOE] = agx['toe_power']
        fitlut.AGX[IX_SHOULDER] = agx['shoulder_power']

    def render(self, src: Path, ev: float, agx: dict) -> Path:
        key = hashlib.md5(json.dumps(
            [src.name, round(ev, 4),
             [round(agx[k], 5) for k in sorted(agx)]]).encode()).hexdigest()
        out = self.cache / f'{key}.png'
        if out.exists():
            return out
        self._agx_apply(agx)
        xmp = self.cache / f'{key}.xmp'
        fitlut.make_xmp(src.name, xmp, ev=ev)
        part = self.cache / f'{key}.part.png'
        part.unlink(missing_ok=True)
        r = subprocess.run(
            ['darktable-cli', str(src), str(xmp), str(part),
             '--core', '--disable-opencl',
             '--configdir', str(self.cache / 'cfg'), '--library', ':memory:',
             '--conf', 'write_sidecar_files=never',
             '--conf', 'plugins/darkroom/workflow=scene-referred (sigmoid)',
             '--conf', 'plugins/darkroom/chromatic-adaptation=modern'],
            capture_output=True, text=True)
        if not part.exists():
            sys.exit(f'darktable-cli failed ({src.name}):\n{r.stderr[-800:]}')
        part.replace(out)
        return out

    def batch(self, evs: dict, agx: dict):
        outs = {}
        for name in IMAGES:
            outs[name] = self.render(self.imgdir / f'{name}.CR3',
                                     evs[name], agx)
        outs['__grid__'] = self.render(self.syn_dng, fitlut.BASE_EV, agx)
        return outs


# --------------------------------------------------------------- data prep
def load_refs(imgdir):
    refs = {}
    for name in IMAGES:
        refs[name] = ImageOps.exif_transpose(
            Image.open(imgdir / f'lightroom_{name}.jpg')).convert('RGB')
    return refs


def seg_pixels(img: Image.Image, rect, step=3):
    w, h = img.size
    x0, y0, x1, y1 = (int(rect[0]*w), int(rect[1]*h),
                      int(rect[2]*w), int(rect[3]*h))
    a = np.asarray(img.crop((x0, y0, x1, y1)), np.float32) / 255.0
    return a[::step, ::step].reshape(-1, 3)


def seg_median(img: Image.Image, rect):
    w, h = img.size
    x0, y0, x1, y1 = (int(rect[0]*w), int(rect[1]*h),
                      int(rect[2]*w), int(rect[3]*h))
    a = np.asarray(img.crop((x0, y0, x1, y1)), np.float32) / 255.0
    return np.median(a.reshape(-1, 3), axis=0)


def dense_pairs(render_png, ref_img, step=3):
    base = ImageOps.exif_transpose(Image.open(render_png)).convert('RGB')
    ref = ref_img
    if base.size != ref.size:
        base = base.resize(ref.size, Image.LANCZOS)
    a = np.asarray(base, np.float32) / 255.0
    b = np.asarray(ref, np.float32) / 255.0
    g = np.asarray(base.convert('L'), np.float32)
    gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
    gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
    edges = Image.fromarray(((gx + gy) > 8).astype(np.uint8) * 255) \
        .filter(ImageFilter.MaxFilter(3))
    flat = np.asarray(edges) == 0
    h, w = a.shape[:2]
    dy, dx = round(h * 0.05), round(w * 0.05)
    sl = np.s_[dy:h-dy:step, dx:w-dx:step]
    wp = np.where(flat[sl].reshape(-1), 1.0, EDGE_W).astype(np.float32)
    return a[sl].reshape(-1, 3), b[sl].reshape(-1, 3), wp


def read_grid(render_png, nvals):
    im = Image.open(render_png).convert('RGB')
    a = np.asarray(im, np.float32) / 255.0
    PS, W0, H0 = 40, 6240, 4160
    cols = W0 // PS
    sy, sx = a.shape[0] / H0, a.shape[1] / W0
    med = np.zeros((nvals, 3), np.float32)
    for i in range(nvals):
        r, c = divmod(i, cols)
        y0 = int((r*PS+10)*sy); y1 = int((r*PS+30)*sy)
        x0 = int((c*PS+10)*sx); x1 = int((c*PS+30)*sx)
        med[i] = np.median(a[y0:y1, x0:x1].reshape(-1, 3), axis=0)
    return med


# --------------------------------------------------------------- LUT solve
AXG = np.linspace(0, 1, N)
_R, _G, _B = np.meshgrid(AXG, AXG, AXG, indexing='ij')
IDENT = np.stack([_R, _G, _B], -1)


def splat(acc, wacc, a, b, wp):
    g = a * (N - 1)
    i0 = np.clip(np.floor(g).astype(int), 0, N - 2)
    f = g - i0
    for dz in (0, 1):
        for dy in (0, 1):
            for dx in (0, 1):
                w = wp * (np.abs(1-dz-f[:, 0]) * np.abs(1-dy-f[:, 1])
                          * np.abs(1-dx-f[:, 2]))
                idx = (i0[:, 0]+dz, i0[:, 1]+dy, i0[:, 2]+dx)
                np.add.at(acc, idx, b * w[:, None])
                np.add.at(wacc, idx, w)


def solve(acc, wacc):
    lut = (acc + PRIOR * IDENT) / (wacc[..., None] + PRIOR)
    conf = wacc / (wacc + PRIOR)
    delta = lut - IDENT
    for _ in range(16):
        sm = np.zeros_like(delta)
        cnt = np.zeros((N, N, N, 1))
        for axis in range(3):
            for sgn in (1, -1):
                sm += np.roll(delta, sgn, axis=axis) \
                    * np.roll(conf, sgn, axis=axis)[..., None]
                cnt += np.roll(conf, sgn, axis=axis)[..., None]
        alpha = (1.0 - conf)[..., None] * 0.7
        delta = delta * (1 - alpha) + (sm / np.maximum(cnt, 1e-9)) * alpha
    for _ in range(6):
        sm = np.zeros_like(delta)
        for axis in range(3):
            for sgn in (1, -1):
                sm += np.roll(delta, sgn, axis=axis)
        alpha = (0.10 * (1.0 - conf) + 0.03)[..., None]
        delta = delta * (1 - alpha) + (sm / 6.0) * alpha
    return np.clip(IDENT + delta, 0, 1)


def apply_lut(lut, a):
    g = a * (N - 1)
    i0 = np.clip(np.floor(g).astype(int), 0, N - 2)
    f = g - i0
    out = np.zeros_like(a)
    for dz in (0, 1):
        for dy in (0, 1):
            for dx in (0, 1):
                w = (np.abs(1-dz-f[:, 0]) * np.abs(1-dy-f[:, 1])
                     * np.abs(1-dx-f[:, 2]))
                out += lut[i0[:, 0]+dz, i0[:, 1]+dy, i0[:, 2]+dx] * w[:, None]
    return out


def dE(a, b):
    return float(np.sqrt(((srgb_to_lab(a) - srgb_to_lab(b)) ** 2)
                         .sum(-1)).mean())


# --------------------------------------------------------------- fitter
class Fitter:
    def __init__(self, args):
        self.imgdir = Path(args.imgdir)
        self.wd = Path(args.workdir)
        self.wd.mkdir(parents=True, exist_ok=True)
        self.syn = np.load(args.synpairs)      # base(F@BASE_EV) unused; cam, target
        self.syn_vals_n = len(self.syn['cam'])
        self.rend = R(self.wd, self.imgdir, Path(args.syndng))
        self.refs = load_refs(self.imgdir)
        self.segs = json.load(open(HERE / 'segments.json'))
        self.segs = {k: v for k, v in self.segs.items() if not k.startswith('_')}
        self.seg_ref = {}   # (img, seg) -> LR median
        self.seg_ref_pct = {}  # (img, seg) -> LR (L_p10, L_p90)
        self.seg_pix = {}
        for img, d in self.segs.items():
            for sname, sd in d.items():
                self.seg_ref[(img, sname)] = seg_median(self.refs[img], sd['rect'])
                pix = seg_pixels(self.refs[img], sd['rect'])
                L = srgb_to_lab(pix)[:, 0]
                self.seg_ref_pct[(img, sname)] = (float(np.percentile(L, 10)),
                                                  float(np.percentile(L, 90)))
        self.state_file = self.wd / 'state.json'
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())
            if self.state.get('obj_version') != 2:
                print('[resume] objective changed -> best reset (params kept)',
                      flush=True)
                self.state['obj_version'] = 2
                self.state['best_obj'] = None
            else:
                print(f"[resume] best {self.state['best_obj']:.3f}", flush=True)
        else:
            self.state = {'params': default_params(), 'best_obj': None,
                          'log': [], 'evals': 0, 'elapsed_s': 0,
                          'obj_version': 2}
        self.t0 = time.time()
        self.elapsed0 = self.state.get('elapsed_s', 0)
        self.budget_s = args.budget_minutes * 60.0

    def elapsed(self):
        return time.time() - self.t0

    def save(self):
        self.state['elapsed_s'] = round(self.elapsed0 + self.elapsed())
        self.state_file.write_text(json.dumps(self.state, indent=1))

    # ---- per-image EV alignment from neutral segments (pass 1) ----
    def align_evs(self):
        p = self.state['params']
        if p['evs']:
            return
        agx = p['agx']
        print('[align] measuring per-image EVs from neutral segments...',
              flush=True)
        for name in IMAGES:
            png = self.rend.render(self.imgdir / f'{name}.CR3',
                                   fitlut.BASE_EV, agx)
            base = ImageOps.exif_transpose(Image.open(png)).convert('RGB')
            if base.size != self.refs[name].size:
                base = base.resize(self.refs[name].size, Image.LANCZOS)
            ratios = []
            for sname, sd in self.segs[name].items():
                if sd['class'] != 'neutral':
                    continue
                mb = srgb_lin(seg_median(base, sd['rect']))
                mr = srgb_lin(self.seg_ref[(name, sname)])
                ratios.append(float(mr.mean() / max(mb.mean(), 1e-6)))
            g = float(np.median(ratios)) if ratios else 1.0
            p['evs'][name] = fitlut.BASE_EV + math.log2(min(max(g, 0.4), 2.5))
            print(f'  {name}: EV {p["evs"][name]:+.3f}', flush=True)
        self.save()

    # ---- LUT data assembly + objective for a param candidate ----
    def evaluate(self, p, note=''):
        if self.elapsed() > self.budget_s:
            raise KeyboardInterrupt
        agx = p['agx']
        evs = {n: p['evs'][n] + p['ev_global'] for n in IMAGES}
        pngs = self.rend.batch(evs, agx)
        # per-image LUT training data + segment base medians
        per_img = {}
        seg_base = {}
        for name in IMAGES:
            a, b, wp = dense_pairs(pngs[name], self.refs[name])
            base = ImageOps.exif_transpose(
                Image.open(pngs[name])).convert('RGB')
            if base.size != self.refs[name].size:
                base = base.resize(self.refs[name].size, Image.LANCZOS)
            sa, sb, sw = [], [], []
            for sname, sd in self.segs[name].items():
                mb = seg_median(base, sd['rect'])
                seg_base[(name, sname)] = mb
                self.seg_pix[(name, sname)] = seg_pixels(base, sd['rect'])
                sa.append(mb)
                sb.append(self.seg_ref[(name, sname)])
                sw.append(sd['w'] * SEG_BOOST)
            per_img[name] = (a, b, wp, np.array(sa, np.float32),
                             np.array(sb, np.float32), np.array(sw, np.float32))
        # synthetic pairs at current base params (grid rendered with agx)
        syn_base = read_grid(pngs['__grid__'], self.syn_vals_n)
        tgt = srgb_lin(self.syn['target'])
        gain = (2.0 ** p['syn']['ev']) * np.array(
            [1.0, 1.0, p['syn']['tint_b']])
        syn_tgt = np.clip(srgb_enc_(tgt * gain), 0, 1).astype(np.float32)
        syn_w = float(10.0 ** p['syn']['logw'])

        def build(exclude=None):
            acc = np.zeros((N, N, N, 3))
            wacc = np.zeros((N, N, N))
            for name, (a, b, wp, sa, sb, sw) in per_img.items():
                if name == exclude:
                    continue
                splat(acc, wacc, a, b, wp)
                splat(acc, wacc, sa, sb, sw)
            splat(acc, wacc, syn_base,
                  syn_tgt, np.full(len(syn_base), syn_w * 400.0,
                                   np.float32))
            return solve(acc, wacc)

        lut_all = build()

        def seg_obj(lut, only_img=None):
            tot = wtot = 0.0
            for (img, sname), mb in seg_base.items():
                if only_img and img != only_img:
                    continue
                w = self.segs[img][sname]['w']
                out = apply_lut(lut, mb[None, :])[0]
                tot += w * dE(out[None, :], self.seg_ref[(img, sname)][None, :])
                wtot += w
            return tot / wtot

        in_sample = seg_obj(lut_all)
        # distribution term: within-segment L spread must match Lightroom's
        # (median-only anchors let the tone curve stretch skin highlights —
        # the 'waxy bright face' failure mode)
        dist = wtot = 0.0
        for (img, sname), pix in self.seg_pix.items():
            w = self.segs[img][sname]['w']
            L = srgb_to_lab(apply_lut(lut_all, pix))[:, 0]
            r10, r90 = self.seg_ref_pct[(img, sname)]
            dist += w * 0.5 * (abs(float(np.percentile(L, 10)) - r10)
                               + abs(float(np.percentile(L, 90)) - r90))
            wtot += w
        dist /= wtot
        loo = 0.0
        for held in IMAGES:
            lut_h = build(exclude=held)
            loo += seg_obj(lut_h, only_img=held)
        loo /= len(IMAGES)
        obj = 0.6 * in_sample + 0.4 * loo + 0.5 * dist
        self.state['evals'] += 1
        print(f"  eval#{self.state['evals']:<3d} obj {obj:6.3f} "
              f"(in {in_sample:.3f} | loo {loo:.3f} | dist {dist:.3f})  {note}",
              flush=True)
        return obj, lut_all, in_sample, loo

    # ---- interleaved round-robin ----
    def run(self, rounds=4):
        self.align_evs()
        p = self.state['params']
        steps = {tuple(path): s for path, s, lo, hi in SPEC}
        best, lut, ins, loo = self.evaluate(p, 'start')
        if self.state['best_obj'] is None or best < self.state['best_obj']:
            self.state['best_obj'] = best
            self.emit(p, lut, best, ins, loo)
        best = self.state['best_obj']
        for rnd in range(rounds):
            improved = False
            for path, s0, lo, hi in SPEC:          # ONE probe per param, interleaved
                step = steps[tuple(path)]
                base_v = get_p(p, list(path))
                for sgn in (+1, -1):
                    v = min(hi, max(lo, base_v + sgn * step))
                    if abs(v - base_v) < 1e-6:
                        continue
                    q = json.loads(json.dumps(p))
                    set_p(q, list(path), v)
                    obj, lut_c, ins_c, loo_c = self.evaluate(
                        q, f"{'.'.join(path)}={v:.3g}")
                    if obj < best - 1e-3:
                        p = q
                        self.state['params'] = p
                        self.state['best_obj'] = best = obj
                        self.state['log'].append(
                            ['.'.join(path), v, round(obj, 4)])
                        self.save()
                        self.emit(p, lut_c, obj, ins_c, loo_c)
                        improved = True
                        break
            print(f'[round {rnd+1}] best {best:.3f}', flush=True)
            self.save()
            if not improved:
                for k in steps:
                    steps[k] *= 0.5
                if max(steps.values()) < 0.01:
                    break
        print(f'[done] best {best:.3f}', flush=True)

    # ---- outputs ----
    def emit(self, p, lut, obj, ins, loo):
        out = self.wd / 'out'
        out.mkdir(exist_ok=True)
        cube = out / 'EOS RP Lightroom match.cube'
        with open(cube, 'w') as f:
            f.write('TITLE "EOS RP -> Lightroom match (hybrid segment fit; '
                    f'obj {obj:.2f} in {ins:.2f} loo {loo:.2f})"\n')
            f.write(f'LUT_3D_SIZE {N}\n')
            for b in range(N):
                for g in range(N):
                    for r in range(N):
                        v = lut[r, g, b]
                        f.write(f'{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n')
        self.rend._agx_apply(p['agx'])
        mean_ev = float(np.mean([p['evs'][n] for n in IMAGES])) + p['ev_global']

        def lut3d_params(relpath):
            raw = relpath.encode().ljust(512, b'\0')
            raw += struct.pack('<iii', 0, 0, 0)
            raw += b'\0' * (2048*2*3) + b'\0' * 128
            return enc(raw)

        mods = [
            ('exposure', 7, exposure_params(mean_ev), 1),
            ('sigmoid', 3, SIGMOID_OFF[0], 0),
            ('agx', 7, fitlut.agx_params(), 1),
            ('lut3d', 3, lut3d_params(cube.name), 1),
        ]
        plugins = []
        for i, (op, ver, blob, en) in enumerate(mods):
            plugins.append(f"""  <plugin>
   <num>{i}</num>
   <module>{ver}</module>
   <operation>{op}</operation>
   <op_params>{blob}</op_params>
   <enabled>{en}</enabled>
   <blendop_params>{BLEND_DEFAULT}</blendop_params>
   <blendop_version>14</blendop_version>
   <multi_priority>0</multi_priority>
   <multi_name></multi_name>
   <multi_name_hand_edited>0</multi_name_hand_edited>
  </plugin>""")
        (out / f'{STYLE}.dtstyle').write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<darktable_style version="1.0">\n'
            f' <info>\n  <name>{STYLE}</name>\n'
            f'  <description>Hybrid segment-fitted Lightroom match (obj {obj:.2f}); '
            'neutral agx base, per-image exposure to taste; needs the cube in the '
            'lut3d root. Spektrafilm 5.8 build.</description>\n </info>\n <style>\n'
            + '\n'.join(plugins) + '\n </style>\n</darktable_style>\n')
        tail = '''  <autoapply>0</autoapply>
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
        for op, ver, blob, en in mods:
            if op == 'sigmoid':
                continue
            (out / f'{op} - EOS RP camera colors.dtpreset').write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n<darktable_preset version="1.0">\n'
                f' <preset>\n  <name>EOS RP camera colors</name>\n'
                f'  <description>Part of: {STYLE}</description>\n'
                f'  <operation>{op}</operation>\n  <op_params>{blob}</op_params>\n'
                f'  <op_version>{ver}</op_version>\n  <enabled>{en}</enabled>\n{tail}\n'
                f'  <blendop_params>{BLEND_DEFAULT}</blendop_params>\n'
                '  <blendop_version>14</blendop_version>\n  <multi_priority>0</multi_priority>\n'
                '  <multi_name></multi_name>\n  <multi_name_hand_edited>0</multi_name_hand_edited>\n'
                '  <filter>0</filter>\n  <def>0</def>\n  <format>15</format>\n'
                ' </preset>\n</darktable_preset>\n')
        (out / 'params.json').write_text(json.dumps(p, indent=1))
        print(f'[emit] out/ refreshed (obj {obj:.3f})', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--imgdir', default=str(Path('testing/Canon EOS RP')))
    ap.add_argument('--workdir', default=str(Path('testing/Canon EOS RP/fit-hybrid')))
    ap.add_argument('--syndng', required=True)
    ap.add_argument('--synpairs', required=True)
    ap.add_argument('--budget-minutes', type=float, default=60.0)
    ap.add_argument('--rounds', type=int, default=4)
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--emit-only', action='store_true')
    a = ap.parse_args()
    if a.status:
        sf = Path(a.workdir) / 'state.json'
        if not sf.exists():
            print('no state yet')
            return
        s = json.loads(sf.read_text())
        print(f"evals {s['evals']}, best {s.get('best_obj')}, "
              f"elapsed {s.get('elapsed_s', 0)/60:.0f}m")
        for row in s['log'][-6:]:
            print(' ', row)
        return
    f = Fitter(a)
    if a.emit_only:
        obj, lut, ins, loo = f.evaluate(f.state['params'], 'emit-only')
        f.emit(f.state['params'], lut, obj, ins, loo)
        return
    try:
        f.run(rounds=a.rounds)
    except KeyboardInterrupt:
        print('[stop] budget/interrupt; state saved (out/ holds current best)',
              flush=True)
        f.save()


if __name__ == '__main__':
    main()
