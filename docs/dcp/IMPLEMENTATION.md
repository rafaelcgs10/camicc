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
3. **Turn color calibration off** for DCP-rendered images (or apply one of
   the "EOS RP … (DCP)" styles, which do both steps): the profile does the
   full illuminant adaptation itself, exactly like Adobe/ART (this is what
   the numbers below validate).
4. The **white balance module is the illuminant control**, as in ART and
   Lightroom: the DCP estimates the scene light from whatever WB sets and
   re-blends its matrices/tables accordingly. "As shot" (the default)
   reproduces the validated Adobe rendering; moving WB re-interprets the
   scene, it does not directly scale the pixels (the DCP normalizes that
   part away).
5. Use agx/sigmoid as usual; the DCP replaces only the colorimetry.

GUI niceties: DCP entries are listed as "Camera Model: Profile Name", and
an "only profiles for this camera" checkbox under the input profile combo
(ticked by default, conf `plugins/darkroom/colorin/only_camera_profiles`)
hides .icc/.dcp file profiles whose name doesn't mention the image's
camera; built-in colorspaces and the active profile always show.

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

### Real raws (all 5 in the repo, in-pipe exposure alignment)

| image | segment ΔE | note |
|-------|-----------|------|
| IMG_8736 (town) | 0.29 | |
| IMG_8919 (bird) | 1.30 | bright foliage: single-channel-clip handling differs |
| IMG_9029 (interior) | 0.59 | |
| IMG_9399 (portrait) | 0.27 | |
| 19-43-22-103 (dog) | 0.13 | |
| **mean** | **0.52** | worst single segment 2.21 |

Same harness with the **Adobe Standard** profile (dual-illuminant +
HueSatMap): mean **0.57** (0.52 / 1.14 / 0.53 / 0.67 / 0.00) — the
illuminant-interpolation and HueSatMap paths hold at the same accuracy
on real raws, not just on the synthetic grid.

Exposure alignment is applied in darktable's exposure module (two-pass,
`DCP_INPIPE=1`), not on the exported 8-bit files — post-export gain fakes
clipped highlights and inflates bright-segment errors. Since exposure runs
before colorin, this also aligns the LookTable sampling level per image.
Highlight method (clip vs inpaint-opposed) and demosaic (RCD) were tested:
darktable's defaults are already the best match to ART.

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
   ~2.4 ΔE of the mean and 6–8 ΔE on center-vs-corner segments). Bad
   segments that cluster by image position (center vs corner) are lens,
   not color.
7. **Exposure-align in-pipe, never post-export**: multiplying exported
   8-bit images by a gain fakes clipped highlights (visible in montages as
   flat grey whites) and corrupts bright-segment ΔE. The exposure module
   runs before colorin, so in-pipe EV also aligns table sampling per image
   (mean 1.20 → 0.52 from this change alone).
8. **Watch for invalid flag combos**: `--unadapt true` + color calibration
   off is a broken hybrid (un-adapted matrix, nothing removes the cast).
   One sweep ran that way and "proved" the value scale hurt; rerunning with
   Design A flags (`--unadapt false --cc off`) reversed the conclusion.
9. **init_pipe must calloc**: the DCP fields in colorin's pipe data were
   only initialized by the DCP branch of commit_params, so any pipe
   rendering a non-DCP edit freed uninitialized pointers on cleanup —
   crashing darktable when opening OLD edits (the DCP ones were fine).
   Latent until the 4.5k-profile enumeration churned the heap; made
   deterministic (and A/B-proven against the fixed build) with
   MALLOC_PERTURB_.
10. **ART's final row-normalization** (cam_rgb rows sum to 1 in
   makeXyzCam) looked like a per-channel divergence but measures out as a
   near-scalar per image — chasing it as a matrix difference was a dead
   end; the scalar part is what the exposure alignment absorbs.
11. **The illuminant must refresh at process time, not only in
   commit_params.** A white-balance edit re-commits only the temperature
   module; colorin's commit_params (where dt_dcp_prepare ran) is NOT
   re-run, so the prepared transform kept the old neutral. Worse, the
   as-shot correction in process() divides by the coefficients the pipe
   actually applied — with a stale camera_white the two cancel exactly and
   WB sliders had literally zero effect on a DCP render (only a reopen
   picked the change up; darktable-cli always builds fresh pipes, which is
   why validation never saw it). Fix: process() recomputes the neutral
   from `pipe->dsc.temperature.coeffs` (fallback as-shot) and re-runs
   dt_dcp_prepare when it changed. Cache safety is free — a WB edit
   already invalidates colorin's input hash.

## Validator usage

`testing/dcp/validate_dcp.py --dt-bin <darktable-cli> --tag <name>
--cc off --unadapt false` plus env flags:

| env | effect |
|-----|--------|
| `DT_DCP_TYPE=27` | colorspace enum id of DT_COLORSPACE_DCP in the build |
| `DCP_FILE=<path.dcp>` | profile to test (default Camera Standard) |
| `DCP_IMAGES=a,b` | subset of images |
| `DCP_ARTREF=<dir>` | ART reference folder under testing/Canon EOS RP/ |
| `DCP_NOLENS=1` | disable dt lens module (match ART without corrections) |
| `DCP_INPIPE=1` | two-pass in-pipe exposure alignment (use for final numbers) |
| `DCP_HL=clip` | force dt clip-highlights method |

## Sources

- darktable clone: `~/Documents/darktable-dcp` (piratenpanda spektrafilm
  branch @ e88281a, the exact commit the nix package pins)
- new files: `src/common/dcp.{c,h}`; touched: `src/common/colorspaces.{c,h}`
  (DT_COLORSPACE_DCP, dcp dir scan), `src/iop/colorin.c` (DCP path)
- ART reference source study: rtengine/dcp.cc (two-phase apply), hsdApply,
  step2ApplyTile, ColorTemp::mul2temp
