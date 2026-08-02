# Project notes

Status 2026-08-02: **everything done.** The tool, docs, comparison images,
metrics and the automated testing harness are complete, verified and pushed;
no open tasks remain. This file is kept as a hand-off/context document for
future work on the project.

## What this repo is

`dcp2icc` converts Adobe/RawTherapee/ART DNG camera profiles (`.dcp`) into
ICC input profiles that reproduce the camera color rendering inside
**darktable** (which cannot read DCPs). Written from scratch after
discovering that `dcamprof make-icc` silently drops the DCP
HueSatMap/LookTable (its JSON parser reads only the matrices) and mangles
embedded tone curves (renders ~100x too dark through darktable/lcms2).

Pipeline (dcp2icc/pipeline.py): WB'd camera RGB -> ForwardMatrix -> XYZ(D50)
-> linear ProPhoto HSV -> HueSatMap (dual-illuminant, sRGB or linear encoded)
-> LookTable -> tone curve (per-RGB-channel like the camera, or luminance
mode) -> Lab -> 33^3 CLUT in an ICC v2 `mft2` A2B0 tag (icc.py, own writer,
big-endian, legacy 16-bit Lab encoding: L*652.8, (a|b+128)*256; input shaper
tables x^(1/1.7) for shadow density). DCPs without a ForwardMatrix (e.g.
RawTherapee's old Canon EOS 5D) get one derived from the ColorMatrix via
inversion + Bradford adaptation to D50.

Parser gotcha that bit once: DNG tag ids 0xC7A3=HueSatMapEncoding,
0xC7A4=LookTableEncoding, 0xC7A5=BaselineExposureOffset (off-by-one is easy).

## Validation (all reproducible via testing/)

`testing/compare.py` builds both profile variants from a DCP, renders the
raw through darktable-cli in an isolated configdir, scores against the
out-of-camera JPEG and writes the metrics table + the README montages.
Results on the README's Canon EOS RP shot (mean |diff| vs JPEG, 0-255):
camera look 8.3, ART's own renderer 10.3, colors-only + agx 12.6, darktable
default 12.8. The README numbers and images come from this harness.
(A manual dcamprof comparison measured 13.2 once; kept as a footnote in the
README but de-prioritized — not part of the harness, no need to maintain it.)

darktable-cli gotchas baked into the harness — keep in mind when editing:

- with --configdir, darktable only accepts ICCs inside `<configdir>/color/in/`
  — profiles elsewhere silently fall back to the standard matrix;
- darktable-cli cannot run while the darktable GUI is open unless
  --configdir points elsewhere (database lock);
- the fork's scene-referred workflow defaults the temperature module to
  "camera reference (D65)" even with auto_presets_applied=1 — the harness
  passes `--conf "plugins/darkroom/workflow=display-referred (legacy)"` to
  get as-shot white balance, which DCP-derived profiles require;
- the tone-mapper params blob in testing/dtxmp.py targets the `agx` module
  (scene-referred default of the darktable fork this was developed against);
  upstream darktable users need a sigmoid blob instead (documented in
  testing/README.md).

## Completed milestones

- [x] DCP parser, DNG color pipeline, ICC v2 writer, CLI (`dcp2icc/`).
- [x] ForwardMatrix fallback for ColorMatrix-only DCPs.
- [x] README with install/usage (pip + nix flake), darktable setup
      checklist, harness-generated comparison images and metrics.
- [x] `testing/` harness: generic per-image comparison (any raw + JPEG +
      DCP; external references via --extra), verified to reproduce the
      manual results.
- [x] Profiles generated and installed locally for Canon EOS RP, EOS 5D and
      EOS 6D Mark II (~/.config/darktable/color/in, sources ~/darktable/icc,
      driver ~/darktable/convert-dcp-to-icc.sh).
- [x] Repo on GitHub (rafaelcgs10/dcp2icc), history clean and pushed.

## Ideas if the project is picked up again (nothing pending)

- pytest suite with a small bundled GPLv3 DCP (round-trip parse -> pipeline
  -> ICC, check known CLUT nodes) for CI without raw files.
- `--cct` flag interpolating dual-illuminant matrices/tables at a given
  color temperature instead of the fixed table choice.
- generator for darktable styles (.dtstyle) pairing each "(camera look)"
  profile with the module-settings checklist.
