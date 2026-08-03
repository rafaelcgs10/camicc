# Project notes

Status 2026-08-03: added the **headroom** third ICC variant + a darktable
**style generator** (camicc-styles) that solves the LUT-profile highlight
clipping raised on pixls.us (finestructure/patrakov). Details in the
"Headroom variant" section below. Earlier status (2026-08-02 evening): tool,
docs, packaging (native/Nix/Docker), DCP fetch automation and the
multi-image testing harness complete, validated and pushed. This file is the
hand-off/context document for future work.

## Headroom variant (2026-08-03)

Problem (finestructure on pixls.us, reproduced in the test set): ICC LUT
input profiles clip highlights. darktable feeds them through LittleCMS,
which clamps input to [0,1] device and output to the Lab PCS, so any value
above diffuse white is destroyed at the input-profile stage — per channel,
so blown windows/skies also shift hue. Only IMG_9029 in the Canon EOS RP
folder triggers it hard: 8.6% of the sensor is saturated, ~9.6% of the
frame is >1.0 after white balance (R x2.0, pinned at 2.0 = +1 EV). The
committed scores show it: colors-only lost to darktable's own matrix on
exactly the two images with super-whites (9029, 8919) and nowhere else.

Fix (all validated pixel-exact in the Docker reference, several dead ends
first — see below):
- **headroom ICC** (camicc --variant headroom / all): the CLUT is built
  with 2.7 EV baked in (pipeline.render_clut headroom=2.7): device 1.0 =
  2^2.7 = 6.5x diffuse white. The pipeline is evaluated at the true
  super-white values (apply_table extended to keep value scale
  multiplicative above 1.0 for BOTH linear and sRGB encodings) and the
  result divided by 6.5 to fit the Lab PCS. Grid 65 not 33 (the in-range
  colors span only part of the axis; 33 loses ~0.2, 65 restores it).
  HEADROOM_EV / HEADROOM_GRID live in pipeline.py, single-sourced.
- **exposure sandwich** in darktable: main exposure -2.0 EV BEFORE colorin
  (nothing reaches the LUT above 1.0), +2.7 EV restored AFTER colorin. Net
  +0.7 EV into sigmoid, highlights intact. TWO equivalent ways to place the
  after-colorin gain (verified pixel-identical, mean 0.000):
  * harness (testing/dtxmp.py make_xmp headroom_ev=...): a SECOND exposure
    instance moved after colorin via a custom iop-order list on the image
    (iop_order_version=0 + iop_order_list with "exposure,1" after colorin).
    Works via XMP.
  * styles (camicc-styles): the **basicadj** module at +2.7 EV. basicadj
    sits after colorin in darktable's DEFAULT pipe order, so NO custom order
    is needed. This matters because darktable-cli does NOT apply a style's
    custom iop-order to a multi-instance (verified: the 2nd exposure lands
    before colorin at order 2600, breaking the sandwich, mean 18 vs the
    correct render). basicadj (single instance, default position) applies
    cleanly. basicadj_params = pure +EV gain (all other fields neutral);
    reproduces a post-colorin exposure instance exactly.

Results (Docker, vs Lightroom): IMG_9029 colors-only 14.1 -> headroom 10.7
(now beats darktable's matrix 10.9); clip-free IMG_9399 unchanged
(colors-only 10.2, headroom 10.1). Headroom never loses to colors-only on
any image/reference; folder-avg vs Lightroom 13.4 -> 12.5.

KEY TRAP (cost hours): color calibration (channelmixerrgb) CANNOT be used
as the post-profile gain. Enabling it at "gain 1.0" already casts the whole
image (R +5, B -10): it re-adapts the already-balanced white balance from
the image, regardless of its illuminant params. Every early headroom
measurement was contaminated by this warm cast (which flattered 9029 and
hurt 9399, masking the real result). The gain MUST be a second exposure
instance. Styles carry channelmixerrgb DISABLED for the same reason.

Dead ends ruled out empirically:
- Lab-16 PCS quantization: NOT the residual (numpy emulation: float vs
  quantized nodes identical). CLUT interpolation over the stretched domain
  is the only real precision cost, fixed by grid 65.
- tone equalizer as the pre-profile compressor (user suggestion): far
  WORSE (9029 vs JPEG 25-28 vs headroom's 11.5). It permanently darkens
  blown highlights to duck under the ceiling, leaving them gray where the
  camera/Lightroom render them white; the exposure sandwich is exactly
  reversible and dominates it. Its mask is scene-normalized (needs
  per-image exposure_boost) which made the naive attempt score 59-70.

## camicc-styles (2026-08-03)

New console script (camicc/styles.py) generating .dtstyle files for the
headroom variant. camicc/dtparams.py is the single source of the validated
darktable param packing (colorin/exposure/basicadj/sigmoid/blend + the
disabled channelmixerrgb/filmicrgb/basecurve blobs + the harness's
iop-order helpers), shared by the shipped generator and testing/dtxmp.py
(which now imports from it — refactor verified to produce byte-identical
XMPs). The .dtstyle contains: colorin (headroom ICC by BASENAME, resolved
from the user's color/in/), exposure -2.0, basicadj +2.7 (the gain; see
above), sigmoid at the sweep optimum (contrast 1.95 / skew -0.225 vs
Lightroom, unchanged from pre-headroom — re-confirmed by the re-sweep), and
channelmixerrgb/filmicrgb/basecurve carried DISABLED. NO custom iop-order in
the style (basicadj is post-colorin by default), so it applies cleanly.
White balance is NOT in the style (as-shot multipliers are per-image) — the
README tells users to set chromatic adaptation to legacy. .dtstyle format &
data.db schema (styles/style_items, op_params/blendop_params are raw BLOBs =
decoded gzNN/hex): per darktable 5.4 src/common/styles.c. Verified
end-to-end in Docker (testing scratch verify_style.py): style loaded into
data.db (parse .dtstyle -> insert rows), rendered with darktable-cli
--style --style-overwrite, matches the style's module chain as an XMP
(mean 0.1-0.3, p95 1.0). Two traps found while verifying (both explain the
earlier "style diverges" scares, neither is a style defect):
- darktable caches the input-profile scan at STARTUP. If color/in is empty
  when darktable first runs, a FILE colorin later reports "icc ... not
  available" and falls back. Fix = install the ICC BEFORE darktable starts;
  for users this is exactly "install, then RESTART darktable" (already in
  the README). (Turned out not even to change the pixels here, but real.)
- the style omits the lens module (geometry, not color); comparing it to an
  XMP that HAS lens shows a big edge diff (mean ~12-16, max 200+ on the
  24mm IMG_9029). Compare against a no-lens module-matched XMP -> ~0.
Also: darktable-cli options (--style, --style-overwrite) go BEFORE --core;
anything after --core is passed to the core (putting --style after --core
prints the help and fails). darktable-cli does NOT apply a style's custom
iop-order to multi-instances (why basicadj, not a 2nd exposure instance).
Styles imported by users via the GUI (lighttable > styles > import); no
headless style import in darktable-cli, and no xvfb in the image.
RENAME: the project is now **camicc** (GitHub repo renamed by
the user; old dcp2icc URLs redirect). The local checkout dir
may still be ~/Documents/dcp2icc. Deprecated compatibility
kept for one release: dcp2icc/dcp2icc-fetch-dcps CLI aliases,
$DCP2ICC_* env vars, nix attr .#dcp2icc.

## What this repo is

`camicc` converts Adobe/RawTherapee DNG camera profiles (`.dcp`) into ICC
input profiles that reproduce the camera color rendering inside
**darktable** (which cannot read DCPs). Written from scratch after
discovering that dcamprof's matrix-only conversion cannot carry the DCP
HueSatMap/LookTable, which hold most of the "camera look".

Pipeline (camicc/pipeline.py): WB'd camera RGB -> ForwardMatrix -> XYZ(D50)
-> linear ProPhoto HSV -> HueSatMap (dual-illuminant, sRGB or linear encoded)
-> LookTable -> tone curve (per-RGB-channel like the camera, or luminance
mode) -> Lab -> 33^3 CLUT in an ICC v2 `mft2` A2B0 tag (icc.py, own writer,
big-endian, legacy 16-bit Lab encoding: L*652.8, (a|b+128)*256; input shaper
tables x^(1/1.7) for shadow density). DCPs without a ForwardMatrix get one
derived from the ColorMatrix via inversion + Bradford adaptation to D50.
Parser gotcha that bit once: DNG tag ids 0xC7A3=HueSatMapEncoding,
0xC7A4=LookTableEncoding, 0xC7A5=BaselineExposureOffset.

Genericity: all 4,465 DCPs on this machine parse and convert cleanly
(incl. 3D HueSatMaps, ColorMatrix-only, sparse curves). The two former
gaps were fixed 2026-08-02: ILLUMINANT_XYZ/_CCT now include codes
3/4/14/22, and ProfileToneCurve is evaluated with a natural cubic spline
per the DNG spec (max change on Adobe's 128-point curves: 0.0009 — real
only for sparse curves).

CCT interpolation (added 2026-08-02): `camicc --cct <K>` and the harness
(automatic, per image from the raw's as-shot WB via estimate_cct;
--no-cct disables) interpolate dual-illuminant matrices + HueSatMaps
DNG-style (linear in 1/CCT). KEY FINDING: Adobe's "Camera *" profiles
are ILLUMINANT-INVARIANT (FM1==FM2, no HueSatMap; the look is all in
the single LookTable+curve) — CCT is a no-op there and the CLI says so
(pipeline.illuminant_dependent). It matters for "Adobe Standard" (mean
dE 8.7 at 2856K vs daylight, EOS RP) and RT/ART profiles (dE 15.8).
Suite + sweep re-run in the rebuilt Docker image after the change:
results identical to the committed ones (expected — the suite tests
Camera-style profiles on daylight-range shots, CCT weights 0.05-0.24).

## The three ways (all validated)

1. **Native** (validated end-to-end in a stock ubuntu:26.04 container):
   `apt install innoextract [darktable rawtherapee libimage-exiftool-perl]`,
   venv `pip install .` -> `camicc`, `camicc-fetch-dcps`; testing via
   `python3 testing/suite.py` etc.
2. **Nix**: `nix run .#` / `.#fetch-dcps`; `nix build .#testing-env` gives
   the pinned toolchain incl. camicc-compare/-suite/-sweep wrappers.
3. **Docker** (Dockerfile + testing/Dockerfile, both multi-stage nix
   builds pinned by flake.lock; nixpkgs = nixos-26.05, darktable 5.4.1,
   RawTherapee 5.12). Built locally with `docker build`; NO CI — the
   GitHub Actions workflow was removed 2026-08-02 at the user's request,
   and all previously published ghcr.io packages (camicc, camicc-testing
   and the pre-rename dcp2icc, dcp2icc-testing) were deleted from GitHub.

**Policy: the Docker testing image is the fixed reference.** Absolute
scores are only comparable within one build — even Ubuntu's identical
5.4.1/5.12 versions score differently than the nix builds, and the
spektrafilm fork differs ~0.3 EV on the EOS RP raw white level.

## DCP acquisition (no Wine, no clicking)

`camicc-fetch-dcps` (camicc/fetch_dcps.py) downloads Adobe DNG Converter
(https://www.adobe.com/go/dng_converter_win, ~1.8 GB, Inno Setup) and
innoextract-unpacks `commonappdata/Adobe/CameraRaw/CameraProfiles` ->
./dcps (~4,370 DCPs). Runtime-only: **the Adobe profiles must never be
committed or redistributed** (dcps/ is gitignored + dockerignored; the
camera test folder carries no DCP, only its sha256 in sources.md).
Default DCP folders: $CAMICC_DCP_DIR (overrides/scopes), ./dcps,
<repo>/dcps (testing only), ~/.cache/camicc/dcps. `camicc` resolves
bare profile names there; with NO argument it converts everything found
(scope with CAMICC_DCP_DIR=dcps/Camera/<model> for a sane --install).

## Testing harness (testing/)

- compare.py: per-raw comparison. Renders camera look / colors only +
  tone mapper / darktable default via generated XMPs (dtxmp.py), plus a
  RawTherapee reference when rawtherapee-cli exists. Metric: EXIF-rotate,
  resize to 480x320, central 80% crop, mean abs RGB diff (0-255) + p95.
  darktable lens correction (embedded-metadata, v10 blob, has_been_set=
  FALSE for per-image autodetect) enabled in every render.
- suite.py: camera folder (testing/Canon EOS RP/) -> per-image
  comparisons + report.md. DCP auto-matched PER REFERENCE: Adobe exports
  name their profile (XMP-crs:CameraProfile, shown in the label, e.g.
  "Lightroom (Camera Standard)") and that wins for that reference; the
  camera JPEG matches via exiftool Model+PictureStyle ("<Model> Camera
  <Style>.dcp", Auto->Standard, fallback Adobe Standard). References with
  different profiles get their own renders/ICCs (shared when equal —
  the common case; validated with a synthetic Camera Portrait export).
  Custom "User Def." styles are REJECTED as ground truth. LICENSE file
  in the folder is mandatory (photos are committed, CC BY-SA 4.0).
- Multiple sources of truth: `<software>_<rawstem>.jpg` next to a raw
  (lightroom_/capture_one_/...) becomes another reference; everything is
  scored per reference group (Picture Style splits the camera group).
- sweep.py: sigmoid parameter search for colors-only. Default = greedy
  adaptive pattern search (axis neighbors, first-improvement move, step
  halving; --init-step 0.45, --min-step 0.15, --patience 2, --tol 0.1,
  render cache shared across reference groups); --search grid = old
  exhaustive grid; --presets opt-in (ranking never changes; search starts
  from the best preset, contrast 1.5/skew 0). --per-image picks each
  image's own best and writes comparison-best-<ref>-<stem>.jpg (the
  README uses the IMG_9399 one). darktable renders are exported at
  RENDER_SIZE=1280 px (compare.py) — 2x faster, scores shift slightly vs
  full-res, which is why all committed artifacts were regenerated.
  KNOWN COST: with 3+ reference groups preferring different regions the
  per-group searches overlap little (~209 renders / ~15 min on the
  5-image folder, similar to the grid); future idea: search a combined
  objective once, then short per-group refinements.
- All tools: self-cleanup by default (--keep to retain), .run.lock
  guards against concurrent runs in the same output dir (concurrent runs
  corrupt each other — happened once).

Key results (Docker reference, 1280px pipeline, Canon EOS RP, Adobe
Camera Standard DCP): vs Lightroom on IMG_9399: camera look 4.1 (< camera
JPEG's 7.6!), colors-only sigmoid defaults 10.2, per-image tuned
c1.95/s-0.225 = 3.0. Folder-average optima: vs Camera JPEG (Standard)
c1.725/s+0.225 = 10.38; vs Lightroom c1.95/s-0.225 = 9.69; vs Camera
JPEG (Auto) c2.175/s-0.225 = 6.81 (off-lattice points the old grid could
not express). Best stock preset is always the scene-referred default.
Per-image optima vary (hard scenes stay 5-10 even at their best). agx
was tested once (presets reconstructed from dt 5.4 source) and always
loses to sigmoid for reference matching.

darktable-cli gotchas baked into the harness — keep in mind when editing:

- with --configdir, ICCs must be inside `<configdir>/color/in/` or they
  silently fall back to the standard matrix;
- `--conf "plugins/darkroom/workflow=display-referred (legacy)"` +
  chromatic-adaptation=legacy for as-shot WB (DCP profiles require it);
- dtxmp.py params blobs are hand-packed from darktable 5.4 structs
  (sigmoid v3, agx v7, lens v10, colorin v7, exposure v7, blend v14);
  module version bumps upstream will need new blobs;
- HOME in containers = the mounted work dir -> darktable/RT drop
  .cache/.config there (gitignored, but don't commit them again).

Repo/infra gotchas: nix flakes ignore UNTRACKED files (git add new files
before nix build); docker build needs cwd = repo root (background shells
sometimes lose cwd — use `cd /home/rafael/Documents/dcp2icc &&`);
photos in testing folders are CC BY-SA and need the LICENSE file.

## Ideas / possible next tasks (nothing promised)

- Fix the two pipeline gaps: extend ILLUMINANT_XYZ, spline tone-curve
  interpolation for sparse curves.
- pytest suite (round-trip parse -> pipeline -> ICC, known CLUT nodes),
  run locally (no CI by choice).
- .dtstyle generator pairing each "(camera look)" profile with the
  module-settings checklist.
- A second camera's folder (any RAW+JPEG material) to prove the whole
  fetch -> auto-match -> suite -> sweep chain camera-agnostically.
