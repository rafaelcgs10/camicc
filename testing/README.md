# Comparative testing

`compare.py` automates the comparison used in the top-level README: it builds
ICC profiles from a DCP with dcp2icc, renders a raw file through darktable
with them, and scores every rendering against the out-of-camera JPEG (the
ground truth for "camera colors").

## Requirements

Natively (validated on stock Ubuntu):

```sh
sudo apt install darktable rawtherapee libimage-exiftool-perl innoextract
pip install numpy pillow          # in a venv on PEP-668 systems
```

- `darktable-cli` in `$PATH` (the darktable GUI may be open; the script runs
  with an isolated `--configdir` inside the output directory)
- optionally `rawtherapee-cli` in `$PATH` — if present, a RawTherapee
  default render (native DCP handling) is added to the comparison
  automatically as the reference
- optionally `exiftool` — used to read the Picture Style / camera model
  for labeling, validity checks and DCP auto-matching
- `innoextract` is only needed by `dcp2icc-fetch-dcps`

On Nix, the complete pinned toolchain (darktable, RawTherapee, exiftool,
python deps and the `dcp2icc-compare`/`-suite`/`-sweep`/`-fetch-dcps`
commands) is one build away — this is exactly what the Docker image
contains:

```sh
nix build .#testing-env
./result/bin/dcp2icc-suite testing/Canon\ EOS\ RP
```

Or use the Docker image, which needs nothing on the host and bundles
darktable and RawTherapee at the versions pinned by `flake.lock`.
Build it from the **repository root**, then run it with the same arguments
as `compare.py`, mounting the directory with your test files at `/work`:

```sh
docker build -f testing/Dockerfile -t dcp2icc-testing .

docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" dcp2icc-testing \
    --raw IMG_9399.CR3 --jpeg IMG_9399.JPG \
    --dcp Canon\ EOS\ RP\ Camera\ Standard.dcp -o results
```

`--dcp` accepts any path, but the container only sees what you mount — for
a DCP outside the current directory, mount its folder too and use the
container-side path:

```sh
docker run --rm --user "$(id -u):$(id -g)" \
    -v "$PWD:/work" -v /path/to/my-dcps:/dcps:ro dcp2icc-testing \
    --raw IMG_9399.CR3 --jpeg IMG_9399.JPG \
    --dcp /dcps/My\ Camera\ Standard.dcp -o results
```

(Paths with spaces are shown backslash-escaped, exactly as bash/zsh tab
completion produces them — no quoting needed.)

## Running a test

You need a raw file **and the out-of-camera JPEG of the same shot** (shoot
RAW+JPEG), plus a DCP for the camera:

```sh
python3 testing/compare.py \
    --raw  photos/IMG_9399.CR3 \
    --jpeg photos/IMG_9399.JPG \
    --dcp  dcps/Canon\ EOS\ RP\ Camera\ Standard.dcp \
    -o results/
```

**Important:** the JPEGs should be shot with a standard Picture Style that
the DCP replicates (e.g. Picture Style *Standard* for a "Camera Standard"
DCP). *Auto* is accepted too — it usually equals Standard processing — but
JPEGs shot with a custom/user-defined style are **rejected automatically**
(no DCP reproduces a custom style). The detected style is shown everywhere
the camera JPEG appears, e.g. "Camera JPEG (Standard)" or
"Camera JPEG (Auto)", so you can judge the numbers accordingly.

## Testing a whole folder at once

`suite.py` automates multi-image testing: create a folder named after your
camera containing raw+JPEG pairs, and it compares every pair and writes a
`report.md` with the aggregate table plus a metrics table and side-by-side
montage per image:

```
Canon EOS RP/
  IMG_0001.CR3   IMG_0001.JPG
  IMG_0002.CR3   IMG_0002.JPG
```

The DCP is **auto-matched per image** from the JPEG's camera model and
Picture Style ("Canon EOS RP" + Standard → `Canon EOS RP Camera
Standard.dcp`, Auto counts as Standard, fallback: the camera's "Adobe
Standard" profile), looked up in the default DCP folders
(`$DCP2ICC_DCP_DIR`, `./dcps`, `<repo>/dcps`, `~/.cache/dcp2icc/dcps`) —
populate them once with `dcp2icc-fetch-dcps` (see the top-level README).
A single `.dcp` placed in the camera folder, or `--dcp`, overrides the
auto-match.

```sh
python3 testing/suite.py testing/Canon\ EOS\ RP   # -> .../comparisons/report.md
# or in Docker — run from the repository root, so that both the camera
# folder path and the default ./dcps folder resolve inside /work:
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
    --entrypoint /env/bin/dcp2icc-suite dcp2icc-testing testing/Canon\ EOS\ RP
```

Options: `--dcp`, `--tonemapper`, `-o`.

Outputs per image in `<folder>/comparisons/<image>/`:

- `metrics.md` — mean absolute pixel difference (0–255) and p95 for every
  rendering, best first. The metric is computed on the **central 80 %** of
  the frame, so residual corner differences in lens distortion/vignetting
  don't dominate the score
- `comparison-full.jpg` — labeled side-by-side montage, sorted by
  similarity: the reference first, then every rendering best-first with
  its score in the label

plus the folder-level `comparisons/report.md` collecting everything.
`compare.py` writes the same per-image files into its `-o` directory.
Intermediates (rendered PNGs, XMP sidecars, the darktable config dir) are
**deleted after scoring by default** — pass `--keep` to any of the tools
to keep them for inspection.

What is rendered and scored:

| Rendering | what it shows |
|---|---|
| dcp2icc (camera look) | the DCP's full rendering: color tables + tone curve |
| dcp2icc (colors only) + tone mapper | DCP colors with darktable's scene-referred tone mapping |
| darktable default | baseline: built-in standard matrix + tone mapper |
| RawTherapee (native DCP) | reference: RawTherapee's default processing, which reads the DCP natively (only if `rawtherapee-cli` is available — always there in the Docker image) |

### Multiple sources of truth

The camera JPEG is the primary reference, but a raw can have additional
reference renderings from other software: a file named
`<software>_<rawstem>.jpg` next to the raw (e.g. `lightroom_IMG_9399.jpg`,
exported full-size from the same raw) is picked up automatically as another
source of truth. Both the suite and the sweep then score every rendering
against **each** reference separately — extra `metrics-<software>.md` /
`comparison-<software>.jpg` files per image, extra aggregate/ranking
sections in the reports — and the references are also cross-scored against
each other. Known prefixes get pretty names (`lightroom`, `capture_one`,
`dxo`, `luminar`, `on1`); any other prefix works and is used as the label.
The camera JPEG pair is still required — prefixed references are always
additional.

### Committed camera folders

Per-camera test folders live in this directory (e.g.
[`Canon EOS RP/`](Canon%20EOS%20RP/)) and are committed **with** their
raw+JPEG pairs and the generated reports, so the results can be reproduced
and extended by anyone. Rules:

- a `LICENSE` file for the photographs is **mandatory** (the tools refuse
  to run without one). The expected license is Creative Commons
  By-Attribution Share-Alike:
  <https://creativecommons.org/licenses/by-sa/4.0/>
- the Adobe `.dcp` is copyrighted and **never committed** (blocked by
  `.gitignore`); run `dcp2icc-fetch-dcps` once to populate `dcps/` and the
  tools auto-match it — `sources.md` documents the DCP name and sha256 the
  committed results were produced with
- render intermediates (`*.png`, `*.tif`, `*.xmp`, `dtconfig/`) stay
  untracked; only `report.md` / `sweep-report.md`, `metrics.md` and the
  montage JPEGs are committed

## Sigmoid parameter search

`sweep.py` finds the sigmoid settings that best reproduce each source of
truth (the camera JPEG, plus any prefixed references) with the *colors
only* profile. It takes the same camera folder as `suite.py`, searches
contrast × skew, and writes `sweep-report.md` with a ranking table, the
winning settings and a truth-vs-best montage per reference
(`--presets` additionally scores darktable's five built-in sigmoid
presets — off by default, since their ranking never changes and the
search already starts from the best one, the scene-referred default):

```sh
python3 testing/sweep.py testing/Canon\ EOS\ RP   # -> .../sweep/sweep-report.md
# or in Docker — again from the repository root:
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
    --entrypoint /env/bin/dcp2icc-sweep dcp2icc-testing testing/Canon\ EOS\ RP
```

The default strategy is an **adaptive pattern search** (the 2D analog of a
binary search): starting from `--contrast-start`/`--skew-start` it
tries the axis neighbors (contrast first) and greedily moves to the first
one that improves the score, halving the step once none does — a fraction
of the renders of an exhaustive grid, and renders are cached so multiple
reference groups reuse them. It starts with `--init-step` (default 0.45)
and stops at `--min-step` (default 0.15) or after `--patience` rounds
(default 2) without at least `--tol` (default 0.1) score improvement.

`--search grid` runs the exhaustive grid instead, each axis configured as
start + step + number of steps:

```sh
python3 testing/sweep.py testing/Canon\ EOS\ RP --search grid \
    --contrast-start 1.5 --contrast-step 0.15 --contrast-steps 5 \
    --skew-start 0 --skew-step 0.15 --skew-steps 4
```

That is 5 × 4 = 20 combinations = 20 darktable renders per image — a few
seconds each.
`--per-image` additionally picks each image's *own* best configuration
(scene content shifts the optimum) and writes an individual truth-vs-best
montage per image (`comparison-best-<reference>-<image>.jpg`).

## Adding external references

`--extra NAME=PATH` (repeatable) adds any image you rendered yourself from
the same raw in some other program as an extra labeled panel and metrics
row. For example, if you exported the raw from Lightroom as `lr.tif`:

```sh
python3 testing/compare.py ... --extra Lightroom=lr.tif
```

## Adding a new test image

Nothing to configure — every test is one invocation. Just point `--raw`,
`--jpeg` and `--dcp` at the new files and use a fresh `-o` directory. For
meaningful "camera look" scores the JPEG's picture style should match the
DCP (e.g. shoot with Picture Style *Standard* and test the
"Camera Standard" DCP).

## How the comparison works

Every rendering is exported by darktable at 1280 px (longest side — the
metric needs far less, and it renders 2–3× faster than full resolution),
then every rendering and the reference image are loaded, rotated according
to their EXIF orientation, and downscaled to a common 480×320 frame
(Lanczos). The outer 10 % border on every side is then discarded — only
the **central 80 %** of the frame is compared, so residual corner
differences in lens distortion and vignetting don't dominate. On what
remains, the score is the **mean absolute difference** over all RGB
values on the 0–255 scale; **p95** is the 95th percentile of those same
absolute differences (how bad the worst areas are, ignoring the extreme
5 %). Because of the strong downscaling, the metric measures color and
tone, not sharpening or noise. 0 means identical; renderings below ≈ 5
are hard to tell apart by eye; above ≈ 15 the difference in look is
obvious.

## Interpreting results

The residual difference of a good profile is dominated by things a color
profile cannot express: per-image tone adaptation (Auto Lighting
Optimizer, RawTherapee's auto-matched curve) and in-camera
sharpening/noise reduction. Reference values from the committed
[`Canon EOS RP/`](Canon%20EOS%20RP/) folder (Docker image, sigmoid):
"camera look" scores 4.8–17 depending on the scene and *beats* RawTherapee
on some images; against a Lightroom export of the same raw it lands within
≈ 4 — closer to Lightroom than the camera JPEG itself, which is expected
since Lightroom implements the same Adobe DCP pipeline that dcp2icc
converts. High-key/high-dynamic-range scenes score worse for the
LUT-profile renders: ICC input profiles clamp highlight reconstruction
before the tone mapper sees it (see the top-level README's limitations).

## Caveats

- The white balance handling is pinned to darktable's *legacy* mode
  (`--conf plugins/darkroom/chromatic-adaptation=legacy` + color calibration
  disabled in the generated XMPs) because DCP-derived profiles expect fully
  white-balanced camera RGB.
- darktable's **lens correction** module is enabled in every render
  (embedded-metadata method, falling back to Lensfun), matching the
  correction the camera applies to its JPEGs.
- `--tonemapper` selects the darktable tone mapper used for the
  "colors only" and "darktable default" renders: `sigmoid` (upstream
  darktable, the default, params = darktable 5.4 module defaults) or `agx`
  (scene-referred default of the spektrafilm darktable fork). Also settable
  via `$DCP2ICC_TONEMAPPER`; the Docker image pins `sigmoid`.
- **Absolute scores are only comparable within one darktable build.** The
  raw black/white calibration darktable applies to a camera can differ
  between versions and raw decoders; e.g. for the Canon EOS RP, upstream
  darktable 5.4 normalizes ~0.3 EV darker than the spektrafilm 5.8 fork,
  which shifts every "vs JPEG" number up by ~4 while leaving the ranking
  unchanged (camera look ≈ 12.6 in the Docker image vs ≈ 8.3 in the fork).
  Compare renderings against each other from the same run, not against
  numbers produced by a different darktable.
