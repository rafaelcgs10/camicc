# Comparative testing

`compare.py` automates the comparison used in the top-level README: it builds
ICC profiles from a DCP with dcp2icc, renders a raw file through darktable
with them, and scores every rendering against the out-of-camera JPEG (the
ground truth for "camera colors").

## Requirements

- `darktable-cli` in `$PATH` (the darktable GUI may be open; the script runs
  with an isolated `--configdir` inside the output directory)
- Python with `numpy` and `Pillow`
  - Nix: `nix-shell -p 'python3.withPackages(ps: [ps.numpy ps.pillow])'`
  - elsewhere: `pip install numpy pillow`

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
- `comparison-full.jpg` — labeled side-by-side montage
- `comparison-crop.jpg` — close-up strip (only with `--crop X0,Y0,X1,Y1`,
  coordinates in a 1200×800 normalized frame; pick a face or colorful detail)
- the rendered PNGs and generated XMP sidecars, for inspection

What is rendered and scored:

| Rendering | what it shows |
|---|---|
| dcp2icc (camera look) | the DCP's full rendering: color tables + tone curve |
| dcp2icc (colors only) + tone mapper | DCP colors with darktable's scene-referred tone mapping |
| darktable default | baseline: built-in standard matrix + tone mapper |

## Adding external references (ART / RawTherapee)

Render the same raw in another program and pass it with `--extra`:

```sh
# ART, using its default processing profile:
ART-cli -d -t -b8 -Y -q -o art_ref.tif -c photos/IMG_9399.CR3

python3 testing/compare.py ... --extra ART=art_ref.tif
```

`--extra` is repeatable (`--extra RT=rt.tif --extra "LR=lightroom.tif"`).

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
compensate). Values from this script on the README's Canon EOS RP test:
camera look ≈ 8.3, ART ≈ 10.3, colors only + agx ≈ 12.6, darktable
default ≈ 12.8 (the README montage used slightly different export/baseline
settings, hence ±0.2 and a 13.8 baseline there; the ranking is identical).

## Caveats

- The white balance handling is pinned to darktable's *legacy* mode
  (`--conf plugins/darkroom/chromatic-adaptation=legacy` + color calibration
  disabled in the generated XMPs) because DCP-derived profiles expect fully
  white-balanced camera RGB.
- The tone-mapper history entry in `dtxmp.py` targets the `agx` module (the
  scene-referred default of the darktable fork this was developed against).
  On upstream darktable, replace `OP_TONEMAPPER`/`TONEMAPPER_PARAMS` in
  `dtxmp.py` with a `sigmoid` params blob copied from one of your own XMP
  sidecars.
