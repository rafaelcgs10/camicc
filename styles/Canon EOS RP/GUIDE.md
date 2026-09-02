# Canon EOS RP "camera colors" with stock darktable modules

Reproduces (approximately) the Canon EOS RP **Camera Standard** color
rendering using **only built-in scene-referred modules** — no ICC/DCP
profile involved. Fitted numerically against Lightroom "Camera Standard"
renderings of the five test raws in `testing/Canon EOS RP/` with
`testing/fitstyle.py`, **under darktable's default modern workflow** —
white balance and color calibration keep their stock behaviour, so the
style drops into an untouched darkroom setup.

**What it can and cannot do:** the Canon look lives in a 3D table
(hue x saturation x brightness) plus a per-channel tone curve. Stock
modules approximate it with per-hue corrections (color equalizer),
a global primaries reshape + tone curve (agx), luminance-zone
saturation (color balance rgb) and a neutral-axis tint (rgb primaries).
Expect "EOS RP character", not a pixel match: mean Lab ΔE ≈ 4–7.5 per
image against Lightroom (the camicc ICC route reaches ≈ 3 — that is the
3D-table gap stock modules cannot close).

## Quick use

- **Style**: import `Canon EOS RP camera colors (native modules).dtstyle`
  in lighttable > styles > import, then apply to your photos. The style
  includes a disabled sigmoid entry, so it automatically switches the tone
  mapper from the workflow default to agx.
- **Presets**: import each `.dtpreset` from the module's preset menu
  (hamburger > import...). Same values as the style, one module at a time
  (remember to disable sigmoid yourself when going this route).

The style assumes darktable defaults elsewhere: white balance "camera
reference", color calibration "as shot" (the modern chromatic-adaptation
workflow), input profile = standard color matrix, lens correction on.
Do not disable color calibration — the fit was made with it active.

## Manual setup, step by step

### 1. exposure
Set exposure to **+0.81 EV** (replace the default +0.5/+0.7).

### 2. color equalizer  (the hue-dependent part of the look)
Leave the top controls at defaults. Set the 8 nodes on the three tabs:

| node     | hue tab | saturation tab | brightness tab |
|----------|---------|----------------|----------------|
| red      | -4.3° | +45.1 % | -8.4 % |
| orange   | -9.9° | +0.7 % | -1.3 % |
| yellow   | +11.5° | +0.5 % | -3.3 % |
| green    | +6.9° | +4.8 % | -3.4 % |
| cyan     | -8.3° | -4.1 % | +4.8 % |
| blue     | -0.1° | +20.8 % | +5.6 % |
| lavender | -5.5° | +6.5 % | +6.7 % |
| magenta  | -2.9° | -9.2 % | +7.9 % |

(The big red saturation boost compensates the red attenuation in the agx
primaries below — together they reshape reds, they are not independent.)

### 3. color balance rgb  (the brightness-dependent saturation)
On the *4 ways* tab (saturation column):
- shadows: **+10 %**
- mid-tones: **+4 %**
- highlights: **+25 %**

Why: the Canon profile's per-channel tone curve boosts shadow chroma and
saturated highlights; measured on the DCP tables directly (see
`testing/dcp_study.py`).

### 4. rgb primaries  (the neutral-axis tint — the white-balance fix)
Enable **rgb primaries** and set, leaving everything else at defaults:
- tint hue: **-112.9°**
- tint purity: **3.50 %**

Why: darktable's as-shot color calibration lands warmer than Lightroom's
as-shot interpretation (measured Δb* +4..+8 on near-neutral pixels), and
no other module in this stack can move grays. This tint counters the
cast: after it, the neutral offset drops to Δb* +0..+3 on the daylight
test set (walls, backdrops and gray fabric render neutral again).
Caveat: it is one global tint fitted on daylight-range shots — under very
different light (tungsten, deep shade) the dt-vs-Lightroom difference
changes, and the usual per-image white-balance tweak is the tool.

### 5. agx  (tone curve + primaries, replaces sigmoid/filmic)
Enable agx, then set:

curve/look:
- curve > contrast: **4.05**
- curve > pivot target output: **0.160**
- curve > toe power: **1.50**
- curve > shoulder power: **2.50**
- look > saturation: **0.75**
- look > preserve hue: **0.85**

primaries tab (attenuation / rotation / purity boost):
- red: **49.5 % / +1.2° / 26.6 %**
- green: **23.4 % / +1.4° / 23.8 %**
- blue: **12.1 % / -1.0° / 9.6 %**

### 6. keep disabled
sigmoid, filmic rgb and base curve must be off (agx is the tone mapper).
The style does this automatically; with presets, do it by hand.

## Fit quality (mean Lab ΔE76 vs Lightroom, Docker reference build)

| image | v3 (this style) | v2 | v1 |
|---|---|---|---|
| IMG_8736       | 5.3 | 5.9 | 8.1 |
| IMG_8919       | 7.5 | 8.0 | 13.2 |
| IMG_9029       | 5.9 | 5.9 | 13.5 |
| IMG_9399       | 3.7 | 5.1 | 8.5 |
| 19-43-22-103   | 5.5 | 5.8 | 15.0 |

v3 = v2 + the rgb-primaries neutral tint and a tone/tint re-fit: the
change is concentrated exactly where neutrals dominate (portrait
backdrop 5.1→3.7, gray sofa 5.8→5.5) and near-neutral surfaces now
match Lightroom's white balance (median Δa*/Δb* roughly halved, down to
+0.1 on the cityscape). v1 was fitted in a legacy-WB environment that
does not match the GUI defaults — the root cause of its yellow-skin cast.

See `montage-vs-lightroom.jpg` for the visual comparison, and
`testing/README.md` ("Fitting the native-modules style") to re-run or
refine the fit — it is resumable and emits this style + presets at any
point.
