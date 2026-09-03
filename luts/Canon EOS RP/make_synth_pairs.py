"""Synthetic pair assembly: G (camicc Camera Standard -> display sRGB) on the
grid values, paired with measured F (darktable base render of the grid DNG)."""
import sys, json
from pathlib import Path
import numpy as np
sys.path.insert(0, '/home/rafael/Documents/dcp2icc')
from camicc.dcp import parse_dcp
from camicc.pipeline import (rgb2hsv, hsv2rgb, apply_table, load_table,
                             spline_curve, bradford_adapt, XYZ2PP, PP2XYZ, WP_D50)
S = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else S / "synthdng"
D65 = np.array([0.95047, 1.0, 1.08883])
A_D50_D65 = bradford_adapt(WP_D50, D65)
XYZ65_TO_SRGB = np.array([[ 3.2404542, -1.5371385, -0.4985314],
                          [-0.9692660,  1.8760108,  0.0415560],
                          [ 0.0556434, -0.2040259,  1.0572252]])
def srgb_enc(x):
    x = np.clip(x, 0, 1)
    return np.where(x > 0.0031308, 1.055*x**(1/2.4) - 0.055, 12.92*x)

dcp = parse_dcp('dcps/Camera/Canon EOS RP/Canon EOS RP Camera Standard.dcp')
FM = np.asarray(dcp.forward_matrix_1, float).reshape(3,3)
vals = np.load(OUT / 'grid_vals.npy')          # WB'd camRGB
Fmeas = np.load(OUT / 'grid_rendered.npy')     # measured darktable base display sRGB

xyz = vals @ FM.T
pp = np.clip(xyz @ XYZ2PP.T, 1e-9, None)
h, s, v = rgb2hsv(pp)
h, s, v = apply_table(h, s, v, load_table(dcp.look_table, dcp.look_table_dims),
                      dcp.look_table_srgb)
pp = hsv2rgb(h, s, v)
tc = np.asarray(dcp.tone_curve, float).reshape(-1, 2)
f = spline_curve(tc[:, 0], tc[:, 1])
pp = f(np.clip(pp, 0, 1))
G = srgb_enc((pp @ PP2XYZ.T) @ A_D50_D65.T @ XYZ65_TO_SRGB.T)

np.savez(OUT / 'synth_pairs.npz', base=Fmeas.astype(np.float32),
         target=G.astype(np.float32), cam=vals.astype(np.float32))
# sanity: neutral ramp — G vs F relationship
neu = np.where(np.all(np.abs(vals - vals[:, :1]) < 1e-9, axis=1))[0]
print('neutral ramp (cam -> F_dt -> G_LR):')
for i in neu[6::6]:
    print(f'  {vals[i,0]:.4f} -> F {Fmeas[i].mean():.3f} -> G {G[i].mean():.3f}')
sat_G = (G.max(1) - G.min(1))[neu]
print('G neutral sat: %.4f' % sat_G.mean())
print('saved synth_pairs.npz:', len(vals), 'pairs')
