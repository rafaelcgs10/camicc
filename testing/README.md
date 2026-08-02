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
    --dcp "Canon EOS RP Camera Standard.dcp" -o results
```

## Running a test

You need a raw file **and the out-of-camera JPEG of the same shot** (shoot
RAW+JPEG), plus a DCP for the camera:

```sh
python3 testing/compare.py \
    --raw  photos/IMG_9399.CR3 \
    --jpeg photos/IMG_9399.JPG \
    --dcp  "dcps/Canon EOS RP Camera Standard.dcp" \
    -o results/
```

Outputs in `results/`:

- `metrics.md` — mean absolute pixel difference (0–255) and p95 against the
  JPEG for every rendering, best first
- `comparison-full.jpg` — labeled side-by-side montage of every rendering
  next to the camera JPEG (this is the image used in the top-level README)
- the rendered PNGs and generated XMP sidecars, for inspection

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
python3 testing/compare.py ... --extra "Lightroom=lr.tif"
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
