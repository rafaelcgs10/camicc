# Work-in-progress notes (for continuing this work in a later session)

Status as of 2026-08-01, late evening. This file is the hand-off context for
Claude (or a human) to continue. Delete once the project is stable.

## What this repo is

`dcp2icc` converts Adobe/RawTherapee/ART DNG camera profiles (`.dcp`) into ICC
input profiles that reproduce the camera color rendering inside **darktable**
(which cannot read DCPs). Written from scratch after discovering that
`dcamprof make-icc` silently drops the DCP HueSatMap/LookTable (its JSON
parser reads only the matrices) and mangles embedded tone curves (renders
~100x too dark through darktable/lcms2).

Pipeline (dcp2icc/pipeline.py): WB'd camera RGB -> ForwardMatrix -> XYZ(D50)
-> linear ProPhoto HSV -> HueSatMap (dual-illuminant, sRGB or linear encoded)
-> LookTable -> tone curve (per-RGB-channel like the camera, or luminance
mode) -> Lab -> 33^3 CLUT in an ICC v2 `mft2` A2B0 tag (icc.py, own writer,
big-endian, legacy 16-bit Lab encoding: L*652.8, (a|b+128)*256; input shaper
tables x^(1/1.7) for shadow density).

## Validation done (all on /rafael_mounts/raw/2026-08-01/IMG_9399.CR3)

- Parser verified against dcamprof dcp2json output for Adobe "Camera Standard"
  and ART's bundled "CANON EOS RP.dcp" (matrices/curve/tables match).
  Watch out: DNG tag ids 0xC7A3=HueSatMapEncoding, 0xC7A4=LookTableEncoding,
  0xC7A5=BaselineExposureOffset (were off-by-one initially).
- Render comparison via darktable-cli (see "How renders were made" below):
  - new tool vs dcamprof-written equivalent CLUT: mean |diff| 0.39/255.
  - new tool "camera look" vs actual camera JPEG: mean |diff| 8.3/255 —
    better than ART's own default render (10.3) and far better than
    dcamprof matrix-only profiles (~32) or darktable defaults (~22).

## How renders were made (needed to regenerate README images)

darktable is the spektrafilm fork (module `agx` replaces sigmoid). Renders are
driven by generated XMP sidecars: see `gen_xmp.py` in the session scratchpad
(recreate if lost: darktable XMP params are zlib+base64 with a "gzNN" prefix;
stack: rawprepare/demosaic/colorin(type=0 + abs path to ICC)/colorout/gamma/
temperature(as-shot coeffs [2.0938,1.0,1.6758])/highlights/agx(off for look
profiles)/channelmixerrgb(disabled)/exposure(0 EV)/flip).

    darktable-cli IMG_9399.CR3 sidecar.xmp out.tif --core \
      --configdir <tmp> --library :memory: --conf write_sidecar_files=never

GOTCHA: when using --configdir, darktable only accepts ICCs that are inside
`<configdir>/color/in/` — profiles elsewhere silently fall back to the
standard matrix. GOTCHA 2: darktable-cli cannot run while the darktable GUI
is open unless --configdir points elsewhere (database lock).

ART reference render: `ART-cli -d -t -b8 -Y -q -o art_ref.tif -c IMG_9399.CR3`
(default dynamic profile = bundled DCP colors + per-image auto-matched neutral
curve + lens/vignette correction; a static ICC cannot reproduce the per-image
curve — corners differ because we don't vignette-correct).

## Status: DONE (2026-08-01)

- [x] README.md with install/usage/darktable setup + comparison images and
      metrics table (docs/img/comparison-full.jpg, comparison-faces.jpg).
- [x] Production profile set regenerated with this tool and installed to
      ~/.config/darktable/color/in (15 profiles: 6 Picture Styles x
      look/colors, Adobe Standard colors, ART colors, ART-match fitted);
      all old/broken profiles deleted.
- [x] ~/darktable/convert-dcp-to-icc.sh now calls this tool.
- [x] Initial git commit. NOT yet pushed — user will create the GitHub repo
      and push (gh CLI not installed here).

## Possible future work

- [ ] Bundle a GPLv3 test DCP from ART in testdata/ with attribution, plus a
      pytest that round-trips parse -> pipeline -> ICC and checks known nodes.
- [ ] Optional dual-illuminant interpolation via a --cct flag (blend the
      HSM1/HSM2 tables and forward matrices at a given color temperature).
- [ ] A darktable style (.dtstyle) generator that pairs each "(camera look)"
      profile with the module settings checklist automatically.
- [ ] Empty testdata/ dir currently in repo — either populate or drop.

## Related local paths

- DCPs: ~/darktable/dcp/ (Adobe, extracted), ART bundled:
  $(nix store path of spektrafilm-art)/share/ART/dcpprofiles/CANON EOS RP.dcp
- Installed profiles: ~/.config/darktable/color/in/
- Older working pipeline (template-based, dcamprof json2icc): ~/darktable/
  build_icc.py + clut-template.json + art_fit_curve3c.json (fitted ART curve
  for IMG_9399, format [[x],[yr],[yg],[yb]] linear sRGB — usable via
  --custom-curve).
