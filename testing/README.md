# Comparative testing

`compare.py` automates the comparison used in the top-level README: it builds
ICC profiles from a DCP with dcp2icc, renders a raw file through darktable
with them, and scores every rendering against the out-of-camera JPEG (the
ground truth for "camera colors").

## Requirements

- `darktable-cli` in `$PATH` (the darktable GUI may be open; the script runs
  with an isolated `--configdir` inside the output directory)
- optionally `rawtherapee-cli` in `$PATH` — if present, a RawTherapee
  default render (native DCP handling) is added to the comparison
  automatically as the reference
- Python with `numpy` and `Pillow`
  - Nix: `nix-shell -p 'python3.withPackages(ps: [ps.numpy ps.pillow])'`
  - elsewhere: `pip install numpy pillow`

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

**Important:** the JPEGs must be shot with a standard Picture Style that the
DCP replicates (e.g. Picture Style *Standard* for a "Camera Standard" DCP).
JPEGs shot with Auto or a custom/user-defined style are not a valid
reference. Check with `exiftool -PictureStyle IMG_1234.JPG`.

## Testing a whole folder at once

`suite.py` automates multi-image testing: create a folder named after your
camera containing raw+JPEG pairs and the `.dcp`, and it compares every pair
and writes a `report.md` with the aggregate table plus a metrics table and
side-by-side montage per image:

```
Canon EOS RP/
  Canon EOS RP Camera Standard.dcp
  IMG_0001.CR3   IMG_0001.JPG
  IMG_0002.CR3   IMG_0002.JPG
```

```sh
python3 testing/suite.py Canon\ EOS\ RP    # -> Canon EOS RP/comparisons/report.md
# or in Docker:
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
    --entrypoint /env/bin/dcp2icc-suite dcp2icc-testing Canon\ EOS\ RP
```

Options: `--dcp` (when the folder has several), `--tonemapper`, `-o`.

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
  `.gitignore`); a `sources.md` documents its name and sha256 so it can be
  fetched from Adobe DNG Converter to reproduce the results
- render intermediates (`*.png`, `*.tif`, `*.xmp`, `dtconfig/`) stay
  untracked; only `report.md` / `sweep-report.md`, `metrics.md` and the
  montage JPEGs are committed

## Sigmoid parameter search

`sweep.py` finds the sigmoid settings that best reproduce the camera JPEG
with the *colors only* profile. It takes the same camera folder as
`suite.py`, grid-searches contrast × skew, always scores darktable's five
built-in sigmoid presets too, and writes `sweep-report.md` ranking every
configuration by its average score over all images:

```sh
python3 testing/sweep.py Canon\ EOS\ RP    # -> Canon EOS RP/sweep/sweep-report.md
# or in Docker:
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
    --entrypoint /env/bin/dcp2icc-sweep dcp2icc-testing Canon\ EOS\ RP
```

Each grid axis is configured as start value + step size + number of steps;
the defaults cover the useful range for JPEG matching:

```sh
python3 testing/sweep.py Canon\ EOS\ RP \
    --contrast-start 1.5 --contrast-step 0.15 --contrast-steps 5 \
    --skew-start 0 --skew-step 0.15 --skew-steps 4
```

That is 5 × 4 = 20 combinations (+ 5 presets) = 25 darktable renders per
image — a few seconds each. `--no-presets` skips the presets; use a finer
`--contrast-step`/`--skew-step` around the winner to refine.

Outputs in `results/`:

- `metrics.md` — mean absolute pixel difference (0–255) and p95 against the
  JPEG for every rendering, best first. The metric is computed on the
  **central 80 %** of the frame, so residual corner differences in lens
  distortion/vignetting don't dominate the score
- `comparison-full.jpg` — labeled side-by-side montage, sorted by
  similarity: camera JPEG first, then every rendering best-first with its
  score in the label (this is the image used in the top-level README)

The intermediate files (rendered PNGs, XMP sidecars, the darktable config
dir) are **deleted after scoring by default** — pass `--keep` to any of the
tools (compare/suite/sweep) to keep them for inspection.

What is rendered and scored:

| Rendering | what it shows |
|---|---|
| dcp2icc (camera look) | the DCP's full rendering: color tables + tone curve |
| dcp2icc (colors only) + tone mapper | DCP colors with darktable's scene-referred tone mapping |
| darktable default | baseline: built-in standard matrix + tone mapper |
| RawTherapee (native DCP) | reference: RawTherapee's default processing, which reads the DCP natively (only if `rawtherapee-cli` is available — always there in the Docker image) |

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

## Interpreting results

The residual difference of a good profile is dominated by things a color
profile cannot express: in-camera sharpening/noise reduction and lens
vignetting correction (enable darktable's *lens correction* module to
compensate). Values from this script on the README's Canon EOS RP test
(spektrafilm fork, `--tonemapper agx`): RawTherapee ≈ 6.2, camera
look ≈ 8.3, colors only + agx ≈ 12.6, darktable default ≈ 12.8.
RawTherapee scoring best is expected — it fits a tone curve per image and
corrects vignetting, which no static profile can; it is the reference
ceiling, and "camera look" is the closest a profile gets inside darktable.

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
