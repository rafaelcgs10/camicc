# Native DCP support in darktable 5.8 (spektrafilm build)

Status: **working, validated against ART**. Patch installed at
`~/nix-configs/home-manager/programs/graphical-apps/darktable-dcp-support.patch`
and wired into `extras.nix` (apply with `nixos switch`).

## What it is

darktable's input color profile module (`colorin`) can now load Adobe DNG
Camera Profiles (`.dcp`) directly and reproduce the Adobe/ART color rendering:

- ColorMatrix1/2 — scene illuminant estimation (DNG self-consistent loop,
  dual-illuminant mixing linear in 1/T with the DNG Robertson table)
- ForwardMatrix1/2 — white-balanced camera RGB → XYZ(D50)
- HueSatMap1/2 and LookTable — applied in linear ProPhoto HSV, Adobe
  reference semantics (sRGB-encoded value axis, index-clamped, pixels never
  clipped by default)
- ProfileToneCurve is **deliberately ignored** — agx/sigmoid/filmic keep
  owning tone. BaselineExposureOffset is read but not applied (matches ART
  with `ApplyBaselineExposureOffset=false`).

## How to use

1. Drop `.dcp` files into `~/.config/darktable/color/dcp/`
   (or `<datadir>/color/dcp/`).
2. Restart darktable; the profiles appear in **input color profile** under
   their DCP names (e.g. "Camera Standard").
3. **Turn color calibration off** for DCP-rendered images: the profile does
   the full illuminant adaptation itself, exactly like Adobe/ART (this is
   what the numbers below validate). Keep the white balance module at its
   default ("as shot") — the DCP path reads the as-shot neutral and corrects
   internally, so WB stays untouched.
4. Use agx/sigmoid as usual; the DCP replaces only the colorimetry.

Config keys (darktablerc, no GUI yet):

| key | default | meaning |
|-----|---------|---------|
| `plugins/darkroom/colorin/dcp_unadapt` | `false` | `true` leaves the illuminant cast in the data for color calibration (Design B; measured worse: ΔE 5.7 vs 3.5) |
| `plugins/darkroom/colorin/dcp_value_scale` | `0.7` | value-axis scale for table sampling; 0.7 matches Adobe/ART headroom, 1.0 puts raw clip at table top |
| `plugins/darkroom/colorin/dcp_clip` | `false` | clip pixel values to [0,1] around table application (ART clips; unbounded preserves scene-referred headroom) |
| `plugins/darkroom/colorin/dcp_tables` | `true` | disable to get the pure matrix rendering |

## Validation vs ART 1.25 (ground truth, no Lightroom)

ART settings: Camera Standard DCP, `ToneCurve=false`, LookTable+HueSatMap on,
WB=Camera, no lens corrections. darktable: DCP input profile, color
calibration off, no tone mapper, lens correction off. Compared via segment
medians (crop-proof) after global exposure alignment on neutral segments,
ΔE76. Harness: `testing/dcp/validate_dcp.py`.

### Real raws (all 5 in the repo)

| image | segment ΔE | note |
|-------|-----------|------|
| IMG_8736 (town) | 0.69 | |
| IMG_8919 (bird) | 2.21 | worst segs are textured foliage — demosaic, not color |
| IMG_9029 (interior) | 0.88 | |
| IMG_9399 (portrait) | 0.69 | |
| 19-43-22-103 (dog) | 1.12 | |
| **mean** | **1.12** | |

### Synthetic patch grid (isolates pure color math, 8.4k patches)

| profile | mean ΔE | median |
|---------|---------|--------|
| Camera Standard | 1.04 (0.76 with per-source value-scale alignment) | 0.73 |
| Camera Faithful | 1.04 | 0.73 |
| Camera Neutral | 1.03 | 0.73 |
| Camera Portrait | 1.16 | 0.78 |
| Camera Landscape | 1.23 | 0.77 |
| Camera Monochrome | 0.52 | 0.33 |
| Adobe Standard (dual-illuminant + HueSatMap) | **0.67** | 0.61 |

## Debugging journey — what mattered (read before touching the code)

1. **Full builds only.** `--target darktable-cli` does not rebuild IOP
   plugins (`build/lib64/darktable/plugins/*.so`); a stale `libcolorin.so`
   silently fell back to the standard matrix for a whole afternoon.
2. **DNG uv conversion**: `den = 1.5 - x + 6y` (not the CIE 1960 form with
   −2x+12y+3). The wrong constant made every image estimate 100000 K.
3. **Normalization is `camera_white = ColorMatrix·XYZ(white_xy)`**
   (dng_color_spec::SetWhiteXY), not the WB multipliers.
4. **ART's WB temperature model is irrelevant here**: its `mul2temp`
   (blackbody/daylight locus on the b/r ratio) lands within 120 K of the DNG
   Robertson CCT on all five test images.
5. **Value-axis scale**: ART samples the LookTable at ~0.7× darktable's
   scene-referred level (dcraw-style max-gain normalization leaves headroom).
   Fixed 0.7 default; measured optimum flat over 0.65–0.75.
6. **Validation fairness**: the two big "color" errors were pipeline
   mismatches — highlight reconstruction (none: identical) and **lens
   vignetting correction** (dt lens module on vs ART off: this alone was
   ~2.4 ΔE of the mean and 6–8 ΔE on center-vs-corner segments).

## Sources

- darktable clone: `~/Documents/darktable-dcp` (piratenpanda spektrafilm
  branch @ e88281a, the exact commit the nix package pins)
- new files: `src/common/dcp.{c,h}`; touched: `src/common/colorspaces.{c,h}`
  (DT_COLORSPACE_DCP, dcp dir scan), `src/iop/colorin.c` (DCP path)
- ART reference source study: rtengine/dcp.cc (two-phase apply), hsdApply,
  step2ApplyTile, ColorTemp::mul2temp
