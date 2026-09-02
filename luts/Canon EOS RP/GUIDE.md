# Canon EOS RP → Lightroom match via 3D LUT (all stock modules)

Matches Lightroom's **Camera Standard** rendering of EOS RP raws inside
darktable using only standard modules: a fixed **neutral tone base**
(exposure + agx with default curve and primaries adjustments disabled)
plus a **3D LUT** (`lut3d` module) fitted from per-pixel pairs of real
renders — darktable base render vs Lightroom export of the same raw.
No DCP/ICC involved: the LUT captures Lightroom's *observed* output,
including engine behavior no profile tag describes.

Why this architecture:
- `lut3d` runs **after** the tone mapper in the pipe (56.5 vs agx 45.5),
  so its [0,1] input clamp only ever sees tone-mapped data — the
  highlight destruction of the ICC route cannot happen, and **agx stays
  enabled**.
- The LUT absorbs everything at once: camera matrix difference, Adobe's
  3D LookTable (including its brightness axis, which no stock hue tool
  can index), the tone-curve residual, the global white-balance
  interpretation difference, and the quirks of the exact darktable build
  it was fitted through.

**Fitted for the spektrafilm darktable 5.8 build** (a numerically fitted
transform encodes the renderer it was fitted through). Refit for another
build with `fitlut.py` — minutes of CPU time.

## Install & use

1. Copy `EOS RP Lightroom match.cube` into your lut3d root folder
   (preferences > processing > 3D LUT root folder, e.g. `~/darktable/luts`).
2. Import `Canon EOS RP Lightroom match (agx + LUT).dtstyle`
   (lighttable > styles > import) and apply it to EOS RP raws.
3. **Adjust exposure per image to taste.** This is part of the design,
   not a workaround: Lightroom's per-image baseline brightness varies a
   lot (+0.7 to +1.3 EV on the test set) and no global transform can
   predict it. The style starts at +1.0 EV; the LUT handles all color.

The style sets: exposure **+1.0 EV** (starting point), sigmoid **off**,
**agx** neutral (default curve, look untouched, primaries adjustments
*disabled*), and **lut3d** with the cube (sRGB space, tetrahedral).
Keep the agx parameters as shipped — the LUT is fitted against exactly
this base. White balance / color calibration / input profile stay at
darktable defaults.

## Validation (mean Lab ΔE76 vs Lightroom, real renders, this build,
each image at its aligned exposure)

| image | agx + LUT | best fitted native style (v4) |
|---|---|---|
| IMG_9399 (portrait) | **2.7** | 3.5 |
| 19-43-22-103 (dog)  | **3.3** | 10.7 |
| IMG_8919 (bird)     | **4.0** | 7.9 |
| IMG_8736 (city)     | **4.3** | 5.0 |
| IMG_9029 (interior) | **4.5** | 5.9 |
| **mean**            | **3.8** | 6.6 |

See `montage-lut-vs-lightroom.jpg`. Honest caveats: these five images
are also the training set (unseen scene colors fall back to smoothed
identity — expect softer accuracy there), and the interior keeps the
largest residual (indoor illuminant differs most from the global
white-balance correction the LUT learned).

## Why the fit recipe looks the way it does (each rule fixed a measured artifact)

- **Neutral, invertible base.** An aggressive base look (the v4 agx with
  saturation 0.75 + primaries attenuation) collapses distinct raw colors
  into one rendered color; the LUT then cannot separate content that
  Lightroom renders differently. The base's only job is invertibility.
- **Per-image exposure alignment, in-pipe.** Fitted unaligned, bright
  content of one image and dark content of another vote in the same LUT
  cells: the dog's 2.5M sofa pixels (Lightroom renders that scene +0.77
  EV brighter) bleached the portrait's 45k hair-sheen pixels. Aligning
  exposure per pair (by re-rendering the base at the aligned EV — the
  exact transform a user's exposure slider applies) removes the conflict.
  The dt-vs-LR *tint* is consistent across images and stays learnable.
- **Edge pixels at weight 0.12.** The two renderers sharpen differently,
  so edge/mixture pixels pair unreliably and mottle sparse cells.
- **Full-resolution training.** Downscaled pairs smear thin structures
  (hair strands) into mixture colors that never existed in either render.
- **gz factor cap.** darktable reads two digits of the params
  compression factor; large zero-filled blobs (lut3d params) compress
  >99:1 and get silently dropped without the cap.

## Refit / add training pairs

    python3 fitlut.py --imgdir "folder with raw + lightroom_*.jpg pairs"

Renders the base through your local `darktable-cli` (two passes: default
EV for alignment measurement, then the aligned EV), fits, and rewrites
the cube. More pairs directly improve unseen-color accuracy.

## Ideas to improve this work

1. **More training pairs — the #1 accuracy lever.** Batch-export more
   raws from Lightroom (Camera Standard, all sliders zeroed, sRGB JPEG,
   named `lightroom_<rawstem>.jpg` next to the raw). With ~20–50 diverse
   scenes, unseen-image error should approach the in-sample ~3 ΔE.
   `fitlut.py` makes the refit a few minutes.
2. **Hybrid DCP-synthetic fill.** Cells no training image covers are
   currently filled by smoothing toward identity. camicc's own DCP
   pipeline could generate *synthetic* pairs (base-model color → DCP
   rendering) to fill exactly those cells, combining the empirical fit
   where data exists with profile math where it doesn't.
3. **Finer LUT (49³/64³) once data supports it.** At 33³ subtle local
   slopes are limited by trilinear cells; more training data would let a
   finer cube capture them without noise.
4. **Per-profile cubes.** The same tool fits Camera Portrait / Landscape
   / Neutral etc. — just export the Lightroom references with that
   profile and refit; ship one cube + style per look.
5. **Exposure-alignment helper.** A tiny script (or darktable Lua) that
   suggests the per-image EV by comparing the base render's neutrals to a
   target midtone level — automating the one manual step the design
   leaves to the user. (Investigating how Lightroom's per-ISO
   BaselineExposure maps to the raw's metadata could make it fully
   deterministic.)
6. **Per-illuminant handling.** The interior's residual (ΔE 4.5) comes
   from the indoor illuminant deviating from the learned global tint. A
   second cube fitted on tungsten/indoor pairs — or a small color
   calibration tweak documented per illuminant class — would close it.
7. **Cross-validation tracking.** `fitlut.py` prints leave-one-out
   scores when >2 pairs; watch them as the training set grows — the gap
   between in-sample and held-out is the honest generalization measure.
8. **Share the technique.** The pixls.us thread (camicc) is the natural
   place: "match any raw developer's look with a fitted display-referred
   LUT after the tone mapper" is camera- and vendor-agnostic, and the
   pitfalls documented above (base invertibility, exposure alignment,
   edge weighting) are the non-obvious parts others would hit too.
9. **Native-DCP module (the long game).** The repo's NATIVE_DCP_STUDY.md
   documents the design for first-class DCP support in darktable
   (characterization in `colorin`, adaptation in color calibration).
   The LUT approach is the pragmatic best-now; the native module remains
   the principled endpoint.
