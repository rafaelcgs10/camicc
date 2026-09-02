"""Characterize what a DCP does to colors, beyond the plain forward matrix,
in terms that map to darktable modules (feeds the fitstyle.py design; see
NATIVE_DCP_STUDY.md section 8 for the Canon EOS RP Camera Standard results).

Decomposition (mirrors the fitting plan):
  baseline = WB'd camRGB -> ForwardMatrix -> XYZ            (what colorin does)
  colorstg = + HueSatMap + LookTable (no tone curve)        (-> colorequal etc.)
  full     = + channel tone curve                           (-> agx + zones)

For a dense sample of ProPhoto HSV values we convert baseline and target to
LCh(D50) and report hue rotation / chroma ratio / lightness ratio binned by
8 hue centers, at several value and saturation levels.

Usage: python3 testing/dcp_study.py "path/to/Camera Standard.dcp"
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camicc.dcp import parse_dcp                              # noqa: E402
from camicc.pipeline import (rgb2hsv, hsv2rgb, load_table,    # noqa: E402
                             apply_table, spline_curve, PP2XYZ)

dcp = parse_dcp(sys.argv[1])

print('== DCP inventory ==')
for k in ['forward_matrix_1', 'forward_matrix_2', 'hue_sat_map_dims',
          'look_table_dims', 'hue_sat_map_srgb', 'look_table_srgb',
          'baseline_exposure_offset', 'calibration_illuminant_1',
          'calibration_illuminant_2']:
    print(f'  {k}: {getattr(dcp, k, None)}')
tc = np.asarray(dcp.tone_curve, dtype=float).reshape(-1, 2) if dcp.tone_curve is not None else None
print(f'  tone_curve points: {None if tc is None else len(tc)}')

FM = np.asarray(dcp.forward_matrix_2 or dcp.forward_matrix_1, float)

# ---- sample: dense HSV grid in *ProPhoto after matrix* --------------------
# We sample directly in ProPhoto HSV (the tables' native domain): hues 0..360,
# sats, values. This is cleaner than sampling camera RGB.
hh = np.linspace(0, 360, 73)[:-1]          # 5 deg steps
ss = np.array([0.15, 0.3, 0.5, 0.7, 0.9])
vv = np.array([0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9])
H, S, V = np.meshgrid(hh, ss, vv, indexing='ij')
h0, s0, v0 = H.ravel(), S.ravel(), V.ravel()
pp0 = hsv2rgb(h0 / 60.0, s0, v0)           # camicc rgb2hsv uses h in [0,6)

def color_stage(pp):
    h, s, v = rgb2hsv(pp)
    hsm = dcp.hue_sat_map_2 if dcp.hue_sat_map_2 is not None else dcp.hue_sat_map_1
    if hsm is not None:
        h, s, v = apply_table(h, s, v, load_table(hsm, dcp.hue_sat_map_dims),
                              dcp.hue_sat_map_srgb)
    pp = hsv2rgb(h, s, v)
    if dcp.look_table is not None:
        h, s, v = rgb2hsv(pp)
        h, s, v = apply_table(h, s, v, load_table(dcp.look_table, dcp.look_table_dims),
                              dcp.look_table_srgb)
        pp = hsv2rgb(h, s, v)
    return pp

def tone_stage(pp):
    f = spline_curve(tc[:, 0], tc[:, 1])
    return f(np.clip(pp, 0, 1))

pp_color = color_stage(pp0)
pp_full = tone_stage(pp_color) if tc is not None else pp_color

# ---- convert to LCh (D50, since ProPhoto is D50) --------------------------
def xyz2lab_(xyz, wp):
    x = xyz / wp
    f = np.where(x > 0.008856, np.cbrt(x), 7.787 * x + 16 / 116)
    return np.stack([116 * f[:, 1] - 16,
                     500 * (f[:, 0] - f[:, 1]),
                     200 * (f[:, 1] - f[:, 2])], -1)

WP_D50 = np.array([0.9642, 1.0, 0.8249])
def lch(pp):
    lab = xyz2lab_(pp @ PP2XYZ.T, WP_D50)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    return L, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360

L0, C0, Hh0 = lch(pp0)
L1, C1, Hh1 = lch(pp_color)
L2, C2, Hh2 = lch(pp_full)

def wrap(d):
    return (d + 180) % 360 - 180

# ---- per-node-bin summary --------------------------------------------------
# colorequal "conventional hue": red=0 (sRGB red primary ~ Lab hue ~40 deg
# for sRGB red... but conventionally people match LCh hue ~ 30 for red).
# We bin by *baseline LCh hue* using the usual color-name centers:
NODE = [('red', 30), ('orange', 55), ('yellow', 90), ('green', 140),
        ('cyan', 200), ('blue', 260), ('lavender', 300), ('magenta', 340)]

def summarize(tag, Lb, Cb, Hb, La, Ca, Ha, mask=None):
    print(f'\n== {tag} ==')
    print(f'{"node":9s} {"dHue":>7s} {"C ratio":>8s} {"L ratio":>8s}   n')
    for name, hc in NODE:
        m = (np.abs(wrap(Hh0 - hc)) < 22.5) & (C0 > 5)
        if mask is not None:
            m &= mask
        if m.sum() == 0:
            print(f'{name:9s}      --')
            continue
        dh = np.median(wrap(Ha[m] - Hb[m]))
        cr = np.median(Ca[m] / np.maximum(Cb[m], 1e-6))
        lr = np.median(La[m] / np.maximum(Lb[m], 1e-6))
        print(f'{name:9s} {dh:+7.1f} {cr:8.3f} {lr:8.3f}   {m.sum()}')

summarize('COLOR stage (HSM+Look) vs matrix — ALL values', L0, C0, Hh0, L1, C1, Hh1)
for vlo, vhi, tag in [(0.0, 0.15, 'shadows V<0.15'), (0.15, 0.5, 'mid V 0.15-0.5'),
                      (0.5, 1.0, 'highlights V>0.5')]:
    summarize(f'COLOR stage — {tag}', L0, C0, Hh0, L1, C1, Hh1,
              mask=(v0 >= vlo) & (v0 < vhi))

for slo, shi, tag in [(0.0, 0.35, 'low sat'), (0.35, 0.75, 'mid sat'), (0.75, 1.0, 'high sat')]:
    summarize(f'COLOR stage — {tag}', L0, C0, Hh0, L1, C1, Hh1,
              mask=(s0 >= slo) & (s0 < shi))

# ---- tone curve on the neutral axis ---------------------------------------
if tc is not None:
    print('\n== tone curve (neutral axis) ==')
    xs = np.array([0.001, 0.01, 0.02, 0.05, 0.1, 0.18, 0.3, 0.5, 0.7, 0.9, 1.0])
    f = spline_curve(tc[:, 0], tc[:, 1])
    ys = f(xs)
    for x, y in zip(xs, ys):
        gain_ev = np.log2(max(y, 1e-6) / max(x, 1e-6))
        print(f'  in {x:6.3f} -> out {y:6.4f}   gain {gain_ev:+.2f} EV')
    # contrast around 0.18 in log-log
    eps = 0.02
    slope = (np.log(f(0.18 + eps)) - np.log(f(0.18 - eps))) / (np.log(0.18 + eps) - np.log(0.18 - eps))
    print(f'  log-log slope @0.18: {slope:.2f}')

# ---- what the CHANNEL curve does to color (vs luminance curve) ------------
if tc is not None:
    print('\n== channel-curve color side-effects (full vs color+lum-curve) ==')
    f = spline_curve(tc[:, 0], tc[:, 1])
    xyz_t = pp_color @ PP2XYZ.T
    Y = np.clip(xyz_t[:, 1], 1e-9, None)
    g = f(np.clip(Y, 0, 1)) / Y
    pp_lum = pp_color * g[:, None]
    Ll, Cl, Hl = lch(pp_lum)
    summarize('channel vs luminance curve — ALL', Ll, Cl, Hl, L2, C2, Hh2)
    for vlo, vhi, tag in [(0.0, 0.15, 'shadows'), (0.15, 0.5, 'mids'), (0.5, 1.0, 'highlights')]:
        summarize(f'channel-curve effect — {tag}', Ll, Cl, Hl, L2, C2, Hh2,
                  mask=(v0 >= vlo) & (v0 < vhi))

# ---- value-axis dependence of the tables ----------------------------------
print('\n== hue-shift spread across V (does one colorequal instance suffice?) ==')
print(f'{"node":9s}', '  '.join(f'V={v:.2f}' for v in vv))
for name, hc in NODE:
    row = []
    for v in vv:
        m = (np.abs(wrap(Hh0 - hc)) < 22.5) & (C0 > 5) & (np.abs(v0 - v) < 1e-6)
        row.append(np.median(wrap(Hh1[m] - Hh0[m])) if m.sum() else np.nan)
    print(f'{name:9s}', '  '.join(f'{x:+6.1f}' for x in row))

print('\n== chroma-ratio spread across V ==')
print(f'{"node":9s}', '  '.join(f'V={v:.2f}' for v in vv))
for name, hc in NODE:
    row = []
    for v in vv:
        m = (np.abs(wrap(Hh0 - hc)) < 22.5) & (C0 > 5) & (np.abs(v0 - v) < 1e-6)
        row.append(np.median(C1[m] / np.maximum(C0[m], 1e-6)) if m.sum() else np.nan)
    print(f'{name:9s}', '  '.join(f'{x:6.3f}' for x in row))

print('\n== chroma-ratio spread across S (full incl. curve, mid V) ==')
print(f'{"node":9s}', '  '.join(f'S={s:.2f}' for s in ss))
for name, hc in NODE:
    row = []
    for s in ss:
        m = (np.abs(wrap(Hh0 - hc)) < 22.5) & (C0 > 5) & (np.abs(s0 - s) < 1e-6) \
            & (v0 >= 0.15) & (v0 < 0.6)
        row.append(np.median(C1[m] / np.maximum(C0[m], 1e-6)) if m.sum() else np.nan)
    print(f'{name:9s}', '  '.join(f'{x:6.3f}' for x in row))

# ---- skin tones specifically ----------------------------------------------
print('\n== SKIN region (baseline LCh hue 20-50, C 15-45, V 0.2-0.7) ==')
m = (Hh0 > 20) & (Hh0 < 50) & (C0 > 15) & (C0 < 45) & (v0 > 0.2) & (v0 < 0.7)
print(f'  n={m.sum()}')
print(f'  color stage:  dHue {np.median(wrap(Hh1[m]-Hh0[m])):+.1f}  Cratio {np.median(C1[m]/C0[m]):.3f}  Lratio {np.median(L1[m]/L0[m]):.3f}')
print(f'  full (curve): dHue {np.median(wrap(Hh2[m]-Hh0[m])):+.1f}  Cratio {np.median(C2[m]/C0[m]):.3f}')
