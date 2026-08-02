# Project notes

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
(incl. 3D HueSatMaps, ColorMatrix-only, sparse curves). Known small gaps,
never fixed: pipeline's ILLUMINANT_XYZ lacks codes 3/4/14/22 (falls back
to D65, affects 2 ColorMatrix-only profiles); tone curve uses linear
interp where the DNG spec says spline (matters only for <16-point curves).

## The three ways (all validated)

1. **Native** (validated end-to-end in a stock ubuntu:26.04 container):
   `apt install innoextract [darktable rawtherapee libimage-exiftool-perl]`,
   venv `pip install .` -> `camicc`, `camicc-fetch-dcps`; testing via
   `python3 testing/suite.py` etc.
2. **Nix**: `nix run .#` / `.#fetch-dcps`; `nix build .#testing-env` gives
   the pinned toolchain incl. camicc-compare/-suite/-sweep wrappers.
3. **Docker** (Dockerfile + testing/Dockerfile, both multi-stage nix
   builds pinned by flake.lock; nixpkgs = nixos-26.05, darktable 5.4.1,
   RawTherapee 5.12). `.github/workflows/docker.yml` pushes to GHCR on
   main — NEVER verified green; check the Actions tab once.

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
  comparisons + report.md. DCP auto-matched per image from exiftool
  Model+PictureStyle ("<Model> Camera <Style>.dcp", Auto->Standard,
  fallback Adobe Standard). Custom "User Def." styles are REJECTED as
  ground truth. LICENSE file in the folder is mandatory (photos are
  committed, CC BY-SA 4.0).
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
- pytest suite (round-trip parse -> pipeline -> ICC, known CLUT nodes)
  wired into CI.
- Verify the GHCR docker publishing workflow is green; then advertise
  `docker pull ghcr.io/rafaelcgs10/...` in the README.
- `--cct` dual-illuminant interpolation.
- .dtstyle generator pairing each "(camera look)" profile with the
  module-settings checklist.
- A second camera's folder (any RAW+JPEG material) to prove the whole
  fetch -> auto-match -> suite -> sweep chain camera-agnostically.
