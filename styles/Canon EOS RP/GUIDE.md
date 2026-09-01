# Canon EOS RP "camera colors" with stock darktable modules

Reproduces (approximately) the Canon EOS RP **Camera Standard** color
rendering using **only built-in scene-referred modules** — no ICC/DCP
profile involved. Fitted numerically against Lightroom "Camera Standard"
renderings of the five test raws in `testing/Canon EOS RP/`
(methodology in the repo notes).

**What it can and cannot do:** the Canon look lives in a 3D table
(hue x saturation x brightness). Stock modules index corrections by hue
only (color equalizer) plus tonal zones (color balance rgb), which
captures roughly a third of the hue behaviour and about half of the
saturation behaviour — the value-dependent part is approximated by the
color balance rgb zone saturation. Expect "EOS RP character", not a
pixel match: for a faithful match use the camicc ICC profiles instead.

## Quick use

- **Style**: import `Canon EOS RP camera colors (native modules).dtstyle` in
  lighttable > styles > import, then apply to your photos.
- **Presets**: import each `.dtpreset` from the module's preset menu
  (hamburger > import...). Same values as the style, applied one module
  at a time.

The style assumes darktable defaults elsewhere (white balance "as shot",
input profile = standard color matrix). Both classic and modern
white-balance workflows are fine.

## Manual setup, step by step

### 1. exposure
Set exposure to **+0.59 EV** (replace the default +0.5/+0.7).

### 2. color equalizer  (the hue-dependent part of the look)
Leave the top controls at defaults. Set the 8 nodes on the three tabs:

| node     | hue tab | saturation tab | brightness tab |
|----------|---------|----------------|----------------|
| red      | -5.6° | +2.3 % | +1.2 % |
| orange   | -4.9° | -0.5 % | -0.9 % |
| yellow   | +2.1° | -3.8 % | -1.1 % |
| green    | +6.0° | -3.7 % | -1.2 % |
| cyan     | -7.0° | -0.1 % | +4.2 % |
| blue     | -10.7° | -0.3 % | +6.2 % |
| lavender | -9.2° | -2.4 % | +7.6 % |
| magenta  | -1.8° | +0.1 % | +5.3 % |

(hue = degrees of rotation at that node; saturation/brightness are the
slider percentages, 0 % = no change.)

### 3. color balance rgb  (the brightness-dependent saturation)
On the *master* tab:
- global saturation: **+0 %**
- contrast: **+0 %**

On the *4 ways* tab (saturation column):
- shadows: **+8 %**
- highlights: **+15 %**

Why: the Canon profile strongly desaturates deep shadows (hides chroma
noise) and boosts saturated highlights; measured zone ratios vs the
native matrix were shadows 0.29 / midtones
0.83 / highlights 1.03.

### 4. agx  (the tone curve, replaces sigmoid/filmic)
Enable agx, keep the default AgX primaries, set:
- curve > contrast: **3.60**
- curve > pivot target output: **0.150**
- look > saturation: **0.92**
- look > slope: **1.00**

### 5. keep disabled
sigmoid, filmic rgb and base curve must be off (agx is the tone mapper).

## Fit quality (mean abs pixel diff vs Lightroom, 0-255, this machine)
| image | this style | darktable default (agx) |
|---|---|---|
| IMG_8736       | 8.0 | 17.6 |
| IMG_8919       | 10.7 | 20.4 |
| IMG_9029       | 9.9 | 11.0 |
| IMG_9399       | 3.4 | 9.6 |
| 19-43-22-103   | 31.4 | 21.8 |
| **mean** | **12.7** | **16.1** |

Four of five images improve substantially (the portrait is nearly a
pixel match). The dog image regresses: Lightroom renders that high-key
scene darker than the folder-average exposure/pivot of this style — a
per-image tone difference, not a color error. Adjust exposure per image
as usual; the color corrections are unaffected.

For a faithful (not approximate) match, the camicc "(colors only)" ICC
route with a tuned tone mapper reaches ~3 on these images — that is the
3D-table gap stock modules cannot close.
