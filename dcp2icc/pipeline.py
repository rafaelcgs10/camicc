"""DNG color pipeline: WB'd camera RGB -> XYZ(D50) -> ProPhoto HSV ->
HueSatMap -> LookTable -> tone curve -> Lab.

This is the part dcamprof's DCP->ICC conversion drops on the floor: the
HueSatMap and LookTable carry most of a camera profile's "look" (hue rotations
and saturation boosts), and the tone curve must be applied per RGB channel to
reproduce the in-camera rendering.
"""
from __future__ import annotations

import numpy as np

# XYZ(D50) <-> linear ProPhoto (RIMM) primaries, D50 white
XYZ2PP = np.array([[ 1.3459433, -0.2556075, -0.0511118],
                   [-0.5445989,  1.5081673,  0.0205351],
                   [ 0.0000000,  0.0000000,  1.2118128]])
PP2XYZ = np.linalg.inv(XYZ2PP)
WP_D50 = np.array([0.9642, 1.0, 0.8249])


def srgb_fwd(x):
    return np.where(x <= 0.0031308, 12.92 * x,
                    1.055 * np.power(np.maximum(x, 0), 1 / 2.4) - 0.055)


def srgb_inv(x):
    return np.where(x <= 0.04045, x / 12.92,
                    np.power((x + 0.055) / 1.055, 2.4))


def rgb2hsv(rgb):
    mx = rgb.max(axis=-1)
    mn = rgb.min(axis=-1)
    d = mx - mn
    h = np.zeros_like(mx)
    m = d > 1e-12
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    idx = m & (mx == r); h[idx] = ((g - b)[idx] / d[idx]) % 6
    idx = m & (mx == g) & (mx != r); h[idx] = (b - r)[idx] / d[idx] + 2
    idx = m & (mx == b) & (mx != r) & (mx != g); h[idx] = (r - g)[idx] / d[idx] + 4
    s = np.where(mx > 1e-12, d / np.maximum(mx, 1e-12), 0.0)
    return h, s, mx


def hsv2rgb(h, s, v):
    h = h % 6
    i = np.floor(h).astype(int)
    f = h - i
    p = v * (1 - s); q = v * (1 - s * f); t = v * (1 - s * (1 - f))
    out = np.zeros(h.shape + (3,))
    for k, chans in enumerate([('v', 't', 'p'), ('q', 'v', 'p'), ('p', 'v', 't'),
                               ('p', 'q', 'v'), ('t', 'p', 'v'), ('v', 'p', 'q')]):
        m = i == k
        loc = {'v': v, 't': t, 'p': p, 'q': q}
        for c in range(3):
            out[m, c] = loc[chans[c]][m]
    return out


def load_table(flat, dims):
    """DNG hue/sat tables are stored value-major: [val][hue][sat] triples."""
    hd, sd, vd = dims
    return np.asarray(flat, dtype=float).reshape(vd, hd, sd, 3)


def apply_table(h, s, v, tab, srgb_enc=False):
    """Apply a DNG HueSatMap/LookTable in HSV space (trilinear, hue wraps)."""
    vd, hd, sd = tab.shape[0], tab.shape[1], tab.shape[2]
    venc = srgb_fwd(np.clip(v, 0, 1)) if srgb_enc else np.clip(v, 0, 1)
    hs = (h / 6.0) * hd
    ss = np.clip(s * (sd - 1), 0, sd - 1 - 1e-9)
    vs = np.clip(venc * (vd - 1), 0, max(vd - 1 - 1e-9, 0)) if vd > 1 else np.zeros_like(venc)
    h0 = np.floor(hs).astype(int) % hd; h1 = (h0 + 1) % hd; hf = hs - np.floor(hs)
    s0 = np.floor(ss).astype(int); s1 = np.minimum(s0 + 1, sd - 1); sf = ss - s0
    v0 = np.floor(vs).astype(int); v1 = np.minimum(v0 + 1, vd - 1); vf = vs - v0

    def tri(c):
        out = 0
        for vv, vw in ((v0, 1 - vf), (v1, vf)):
            for hh, hw in ((h0, 1 - hf), (h1, hf)):
                for sss, sw in ((s0, 1 - sf), (s1, sf)):
                    out = out + tab[vv, hh, sss, c] * vw * hw * sw
        return out

    dh, dsc, dvc = tri(0), tri(1), tri(2)
    h = (h + dh * 6.0 / 360.0) % 6
    s = np.clip(s * dsc, 0, 1)
    if srgb_enc:
        v = srgb_inv(np.clip(venc * dvc, 0, 1))
    else:
        v = np.clip(v * dvc, 0, None)
    return h, s, v


# DNG CalibrationIlluminant codes -> illuminant white XYZ (Y=1)
ILLUMINANT_XYZ = {
    17: np.array([1.09850, 1.0, 0.35585]),   # Standard Light A
    20: np.array([0.95682, 1.0, 0.92149]),   # D55
    21: np.array([0.95047, 1.0, 1.08883]),   # D65
    23: np.array([0.96422, 1.0, 0.82491]),   # D50
}

_BRADFORD = np.array([[ 0.8951,  0.2664, -0.1614],
                      [-0.7502,  1.7135,  0.0367],
                      [ 0.0389, -0.0685,  1.0296]])


def bradford_adapt(src_white, dst_white):
    s = _BRADFORD @ src_white
    d = _BRADFORD @ dst_white
    return np.linalg.inv(_BRADFORD) @ np.diag(d / s) @ _BRADFORD


def forward_matrix_from_color_matrix(cm, illuminant_code):
    """Fallback for DCPs without a ForwardMatrix: invert the ColorMatrix
    (XYZ->camera at the calibration illuminant) and adapt to D50.

    For white-balanced camera RGB the mapping is
    inv(CM) . diag(camera_neutral) followed by Bradford adaptation of the
    calibration illuminant to D50, so that [1,1,1] lands exactly on D50.
    """
    W = ILLUMINANT_XYZ.get(illuminant_code, ILLUMINANT_XYZ[21])
    CM = np.asarray(cm, dtype=float)
    neutral = CM @ W
    M = np.linalg.inv(CM) @ np.diag(neutral)
    return bradford_adapt(W, WP_D50) @ M


def xyz2lab(xyz):
    x = xyz / WP_D50
    f = np.where(x > (6 / 29) ** 3, np.cbrt(np.maximum(x, 1e-12)),
                 x / (3 * (6 / 29) ** 2) + 4 / 29)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def render_clut(dcp, grid=33, shaper_gamma=1.7, look=True, hsm_illuminant=2,
                curve='auto', curve_mode='channel', curve_data=None, pre_ev=None):
    """Compute the Lab CLUT for an input ICC.

    Returns (lab[grid^3, 3], input_table[256]) where input_table maps device
    values to CLUT coordinates (x^(1/shaper_gamma), denser nodes in shadows).
    Node ordering: channel 1 outermost, channel 3 fastest (ICC lut16 layout).

    curve: 'auto' (use the DCP tone curve if present), 'dcp', 'none', or
           'custom' with curve_data = (x, (yr, yg, yb)) applied per sRGB channel.
    curve_mode: 'channel' (per RGB channel in ProPhoto, mimics in-camera
                rendering) or 'luminance' (hue/sat preserving).
    pre_ev: exposure pre-scale in EV; None = use DCP BaselineExposureOffset.
    """
    fm = dcp.forward_matrix_2 or dcp.forward_matrix_1
    if fm is not None:
        FM = np.asarray(fm, dtype=float)
    elif dcp.color_matrix_2 is not None:
        FM = forward_matrix_from_color_matrix(dcp.color_matrix_2,
                                              dcp.calibration_illuminant_2)
    elif dcp.color_matrix_1 is not None:
        FM = forward_matrix_from_color_matrix(dcp.color_matrix_1,
                                              dcp.calibration_illuminant_1)
    else:
        raise ValueError('DCP has neither ForwardMatrix nor ColorMatrix; '
                         'cannot build input profile')

    # grid node -> device value via inverse shaper
    u = np.linspace(0, 1, grid)
    axis = np.power(u, shaper_gamma)
    R, G, B = np.meshgrid(axis, axis, axis, indexing='ij')
    dev = np.stack([R.ravel(), G.ravel(), B.ravel()], axis=-1)

    if pre_ev is None:
        pre_ev = dcp.baseline_exposure_offset
    dev = dev * (2.0 ** pre_ev)

    xyz = dev @ FM.T
    pp = np.clip(xyz @ XYZ2PP.T, 1e-9, None)

    hsm = dcp.hue_sat_map_2 if hsm_illuminant == 2 and dcp.hue_sat_map_2 \
        else dcp.hue_sat_map_1
    if hsm is not None:
        h, s, v = rgb2hsv(pp)
        h, s, v = apply_table(h, s, v, load_table(hsm, dcp.hue_sat_map_dims),
                              dcp.hue_sat_map_srgb)
        pp = hsv2rgb(h, s, v)
    if look and dcp.look_table is not None:
        h, s, v = rgb2hsv(pp)
        h, s, v = apply_table(h, s, v, load_table(dcp.look_table, dcp.look_table_dims),
                              dcp.look_table_srgb)
        pp = hsv2rgb(h, s, v)

    if curve == 'auto':
        curve = 'dcp' if dcp.tone_curve is not None else 'none'
    if curve == 'dcp':
        tc = np.asarray(dcp.tone_curve, dtype=float).reshape(-1, 2)
        cx, cy = tc[:, 0], tc[:, 1]
        if curve_mode == 'channel':
            pp = np.interp(np.clip(pp, 0, 1), cx, cy)
        else:
            xyz_t = pp @ PP2XYZ.T
            Y = np.clip(xyz_t[:, 1], 1e-9, None)
            g = np.interp(np.clip(Y, 0, 1), cx, cy) / Y
            pp = pp * g[:, None]
    elif curve == 'custom':
        cx, (cyr, cyg, cyb) = curve_data
        XYZ2SRGB = np.array([[3.1338561, -1.6168667, -0.4906146],
                             [-0.9787684, 1.9161415, 0.0334540],
                             [0.0719453, -0.2289914, 1.4052427]])  # D50-adapted
        srgb = np.clip((pp @ PP2XYZ.T) @ XYZ2SRGB.T, 0, 1)
        srgb = np.stack([np.interp(srgb[:, 0], cx, cyr),
                         np.interp(srgb[:, 1], cx, cyg),
                         np.interp(srgb[:, 2], cx, cyb)], axis=-1)
        pp = np.clip(srgb @ np.linalg.inv(XYZ2SRGB).T @ XYZ2PP.T, 0, None)

    lab = xyz2lab(pp @ PP2XYZ.T)
    xs = np.linspace(0, 1, 256)
    input_table = np.power(xs, 1 / shaper_gamma)
    return lab, input_table
