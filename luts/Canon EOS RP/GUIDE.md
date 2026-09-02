# Canon EOS RP → Lightroom match via 3D LUT (all stock modules)

Matches Lightroom's **Camera Standard** rendering of EOS RP raws inside
darktable using only standard modules: a fixed tone base (exposure + agx)
plus a **3D LUT** (`lut3d` module) fitted from per-pixel pairs of real
renders — darktable base render vs Lightroom export of the same raw.

Why this architecture:
- `lut3d` runs **after** the tone mapper in the pipe, so its [0,1] input
  clamp only ever sees tone-mapped data — the highlight destruction of
  the ICC route cannot happen, and **agx stays enabled**.
- The LUT absorbs everything at once: camera matrix difference, Adobe's
  3D LookTable (including its brightness axis, which no stock hue tool
  can index), the tone-curve residual, and the quirks of the exact
  darktable build it was fitted through.

**Fitted for the spektrafilm darktable 5.8 build.** Refit for other
builds with `fitlut.py` (below) — it is a few minutes of CPU time.

## Install

1. Copy both `.cube` files into your lut3d root folder
   (preferences > processing > 3D LUT root folder, e.g. `~/darktable/luts`).
2. Import `Canon EOS RP Lightroom match (agx + LUT).dtstyle`
   (lighttable > styles > import) and apply it to EOS RP raws.

The style sets: exposure **+0.51 EV**, sigmoid **off**, **agx** with the
fitted tone (contrast 4.05, pivot 0.160, toe 1.65, shoulder 2.10,
look saturation 0.75, preserve hue 0.70, fitted primaries), and **lut3d**
pointing at `EOS RP Lightroom match.cube` (sRGB space, tetrahedral).
White balance / color calibration / input profile stay at darktable
defaults. Keep the agx parameters as shipped — the LUT was fitted
against exactly this base; exposure and white balance are yours to
adjust per image as usual.

## Validation (mean Lab ΔE76 vs Lightroom, real renders, this build)

| image | agx + LUT | best fitted native style (v4) |
|---|---|---|
| IMG_8736      | 4.7 | 5.0 |
| IMG_8919      | 4.9 | 7.9 |
| IMG_9029      | 4.9 | 5.9 |
| IMG_9399      | 2.9 | 3.5 |
| 19-43-22-103  | 4.7 | 10.7 |
| **mean**      | **4.4** | 6.6 |

Honest caveat: these five images are also the training set. Leave-one-out
cross-validation puts unseen-scene error around ΔE 6 (worse for scene
colors the training pairs never covered). **The fix is more training
pairs**: export more of your raws from Lightroom (Camera Standard, all
adjustments zeroed, sRGB JPEG named `lightroom_<rawstem>.jpg` next to the
raw) and refit — accuracy approaches the ~3 in-sample level as coverage
grows.

The second cube, `EOS RP Lightroom match (neutral-aligned).cube`, was
fitted after normalizing each pair on the neutral axis: use it if you
fine-tune WB/exposure per image anyway (it generalizes better to new
scenes but expects that per-image alignment).

## Refit / add training pairs

    python3 fitlut.py --imgdir <folder with raw + lightroom_*.jpg pairs>

renders the base through your local `darktable-cli`, fits both cubes and
rewrites them, printing per-image and leave-one-out scores.
