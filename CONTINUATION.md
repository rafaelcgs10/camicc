# Project notes

## HAND-OFF 2026-09-02: EOS RP native style v2 + neutral-tint refit pending

Branch `eos-rp-native-style` (pushed). The native-modules style was refit
with the new `testing/fitstyle.py` (commit "styles: refit EOS RP native
style in the modern-workflow environment") — read that commit message and
`testing/README.md` ("Fitting the native-modules style") first. Key facts:

- **Root cause of v1's yellow skin**: v1 was fitted under legacy-WB conf
  but used under the modern workflow. The fitter now renders in the
  modern-workflow environment (WB + color calibration untouched, agx tone
  mapper, sigmoid disabled by the style itself). v2 shipped in
  `styles/Canon EOS RP/` scores dE76 5.1–8.0 vs Lightroom (v1: 8.1–15.0).
- **fitstyle.py essentials**: runs in the Docker image (`camicc-testing`,
  build from testing/Dockerfile); budgeted (`--budget-minutes`), resumable
  (content-addressed render cache in `<workdir>/cache` + `state.json`);
  `--status` = progress/%/ETA; emits current-best style+presets to
  `<workdir>/out` after every stage; `--weights`, `--stages` (subset AND
  order), `--reset`, `--emit-only`, `--report-only`. Fit workdir
  `testing/Canon EOS RP/fit/` is gitignored (1.7 GB cache) — a fresh
  machine starts with an empty cache and NO state.json. The fitted v2
  params are committed as `styles/Canon EOS RP/fitted-params.json`; on a
  fresh machine pass
  `--init-params "styles/Canon EOS RP/fitted-params.json"` (mounted under
  /work) to seed the state — `default_params()` in fitstyle.py is the v1
  style, only the original fit's starting point.
- **NEXT STEP (recommended, ~45 min): neutral-tint refit.** The user
  spotted that v2 still has a white-balance-like warm cast vs Lightroom:
  measured on near-neutral pixels the render sits at db* +4..+7 (yellow),
  da* -1..-2 (green). None of the previously fitted modules can move
  grays (colorequal thresholds low-sat pixels; agx primaries/curve and
  the colorbalancergb saturation columns preserve neutrals) — so the
  optimizer could not fix it. Implemented but NOT yet run: an
  `rgb primaries` module entry (achromatic tint hue/purity only) + a
  `tint` fitting stage (hue-circle sweep at purity 0.015, then coordinate
  refine). Smoke-tested: tint hue ~ -1.57 rad counters the cast
  direction; expect purity ~0.04-0.06. Run:

      docker run --rm --user "$(id -u):$(id -g)" \
          --entrypoint /env/bin/python3 -v "$PWD:/work" camicc-testing \
          /work/testing/fitstyle.py \
          --workdir "/work/testing/Canon EOS RP/fit" \
          --init-params "/work/styles/Canon EOS RP/fitted-params.json" \
          --stages tint,tone,ce1,zones,primaries --budget-minutes 45 \
          --weights "IMG_8736=1.5,IMG_9029=1.5,IMG_9399=1.5"

  (tint first; then re-tune tone/colorequal/zones since large gray areas
  shift. On a fresh machine the cache is cold — the first eval re-renders
  everything, still fine.) Then: `--report-only` for the montage, verify
  neutrals with the scratch `neutralcheck.py` approach (median a*/b* on
  ref-chroma<8 pixels), copy `fit/out/*` into `styles/Canon EOS RP/`,
  update GUIDE.md numbers, replace the user's `~/darktable/styles` +
  `~/darktable/presets` copies, commit.
- **Style-application verify** (already proven for v2, redo after refit):
  inject the .dtstyle into a config's data.db (styles/style_items, blobs
  zlib-decoded), render base-XMP (channelmixerrgb CAT + lens + sigmoid
  enabled) + `--style` over it, compare to the ops_for() XMP render —
  must be pixel-identical. darktable-cli applies NO auto presets on bare
  raws (no lens, no CAT, no orientation!) — always give it the base XMP.
- **DCP ground truth**: `testing/dcp_study.py` + NATIVE_DCP_STUDY.md §8
  (uncommitted local notes): EOS RP Camera Standard has NO HueSatMap,
  FM1==FM2; one LookTable (value-dependent hue rotations) + channel tone
  curve. The ce2 highlight-masked colorequal instance is implemented
  (validated blendif packing, blend_cst=4 mandatory) but the fit keeps it
  off — agx covers the value-dependence for this DCP.

Status 2026-08-02 (evening): tool, docs, packaging (native/Nix/Docker),
DCP fetch automation and the multi-image testing harness are complete,
validated and pushed; nothing in flight. A project rename to **camicc**
was agreed (name vetted: free on GitHub/PyPI, no bad meanings) but NOT
yet executed. This file is the hand-off/context document for future work.
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
