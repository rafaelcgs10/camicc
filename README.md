# camicc

> **Disclaimer**: this project was written almost entirely by an LLM
> (Claude), directed and reviewed by a maintainer who does **not** claim a
> deep understanding of the color-science math behind the DCP → ICC
> conversion. Treat the pipeline as *empirically* validated rather than
> expert-reviewed: every published number and image is reproduced by the
> automated harness in [`testing/`](testing/) against real out-of-camera
> JPEGs and Adobe (Lightroom) renderings. Corrections from people who know
> this domain are very welcome.

Convert DNG camera profiles (`.dcp`) into ICC input profiles that reproduce
the **camera's color rendering inside [darktable](https://www.darktable.org/)**.

darktable cannot read DCP camera profiles — the format used by Adobe and
RawTherapee to describe how a camera's colors should be rendered.
The usual advice is to convert DCP to ICC with dcamprof, but its conversion
is matrix-based: **the HueSatMap and LookTable are not carried over** — and
those 3D color tables hold most of the "camera look" (hue rotations and
saturation boosts, e.g. up to 1.7× on skin tones), so a matrix-only profile
renders noticeably flatter than the camera.

`camicc` implements the full DNG color pipeline instead:

```
white-balanced camera RGB
  → ForwardMatrix → XYZ (D50)
  → linear ProPhoto HSV
  → HueSatMap        (dual-illuminant, linear or sRGB encoding)
  → LookTable        (3D, value axis, sRGB encoding)
  → tone curve       (per RGB channel, like the camera; or hue-preserving)
  → CIELAB
```

…sampled into a 33³ CLUT and written as a self-contained ICC v2 profile that
darktable (via LittleCMS2) applies as an input color profile.

## How close does it get?

Test scene: Canon EOS RP raw (`.CR3`), DCP: Adobe's "Camera Standard"
profile for the EOS RP. All numbers are the mean absolute pixel difference
on the central 80% of the frame (0–255, lower is better; ≲5 is hard to
tell apart by eye), produced by the reproducible harness in
[`testing/`](testing/) inside its pinned Docker image.

**The fair benchmark is Adobe's own rendering, not the camera JPEG.**
camicc converts Adobe's DCP, so the ground truth for the conversion is
what Adobe's pipeline (Lightroom / Camera Raw) produces from the same raw
with that DCP. The camera JPEG adds in-camera processing (auto lighting
optimization, per-image tweaks, sharpening) that no DCP contains — not
even Lightroom matches it exactly. Against a Lightroom export:

![vs Lightroom](docs/img/comparison-lightroom.jpg)

| Rendering | mean diff vs Lightroom |
|---|---|
| **darktable + camicc "(camera look)"** | **4.1** |
| Camera JPEG (Picture Style Standard) | 7.6 |
| darktable + camicc "(colors only, headroom)" + sigmoid | 10.1 |
| darktable + camicc "(colors only)" + sigmoid | 10.2 |
| darktable factory default (sigmoid, standard matrix) | 10.5 |
| RawTherapee default (native DCP) | 11.4 |

`camicc (camera look)` lands **closer to Lightroom than the camera's own
JPEG does** — the DCP pipeline (color tables + tone curve) survives the
conversion to ICC essentially intact. Even RawTherapee, which reads the
DCP natively but applies its own per-image curve, is further away.

### The three profile variants

Each DCP converts into three ICCs:

- **`(camera look)`** carries the DCP tone curve inside the profile — the
  faithful match above (4.1), at the price of switching darktable's own
  tone mapping off.
- **`(colors only)`** carries only the DCP color tables and leaves the
  tone curve to darktable's scene-referred tone mapper (sigmoid / filmic /
  agx), keeping darktable's full highlight handling. With sigmoid at its
  defaults it scores 10.2 vs Lightroom — the colors are already right, the
  entire difference is tone curve shape.
- **`(colors only, headroom)`** is *(colors only)* with **2.7 EV of
  highlight headroom baked in**, immune to the highlight clipping that LUT
  input profiles otherwise suffer (see below). It needs a small exposure
  setup, so it ships with a one-click darktable style — see
  [Highlight headroom](#highlight-headroom-the-headroom-variant).

That difference is two sliders away: sigmoid at **contrast 1.95,
skew −0.225** brings *(colors only)* to **3.0** vs Lightroom on the same
image — matching *(camera look)*, while staying fully scene-referred:

![Lightroom vs tuned colors-only](docs/img/comparison-best-lightroom-IMG_9399.jpg)

The tuned settings were found automatically by the parameter sweep in
[`testing/`](testing/). It is not always this close: the optimum shifts
with scene content, and on harder scenes (backlit skies, heavy in-camera
lifting) even the per-image best sigmoid stays at a visible 5–10 — see the
[per-image tables and montages](testing/Canon%20EOS%20RP/sweep/sweep-report.md)
for the whole test set.

### Highlight headroom (the headroom variant)

An ICC LUT input profile has a hard limit: darktable applies it through
LittleCMS, which **clamps every value above diffuse white to 1.0 before the
tone mapper sees it** — per channel, so blown windows and skies both flatten
*and* shift hue. On a scene with bright clipped highlights (a sunlit window,
a sunset) the plain *(colors only)* profile loses to darktable's own matrix
profile for exactly this reason.

The **`(colors only, headroom)`** variant fixes it. The CLUT is built to
expect input pre-scaled down by 2.7 EV, so device 1.0 corresponds to 6.5×
diffuse white; the DCP color pipeline is evaluated at the true (super-white)
values inside the profile and scaled back to fit. In darktable the exposure
is split around the input profile — **−2 EV before it** (so nothing reaches
the LUT above 1.0) and **+2.7 EV restored after it** — a net +0.7 EV into
the tone mapper with highlights fully intact. The one-click
[darktable style](#darktable-styles-one-click-setup) sets all of this up.

On the test set's one strongly-clipped scene (a room with two blown
windows, 24 mm), against a Lightroom export:

| Rendering | mean diff vs Lightroom |
|---|---|
| **camicc `(colors only, headroom)`** | **10.7** |
| darktable factory default (matrix + sigmoid) | 10.9 |
| camicc `(colors only)` (highlights clipped) | 14.1 |

The headroom variant turns the LUT profile's worst case into a rendering
that beats darktable's own matrix, and on scenes *without* clipping it is
identical to plain *(colors only)* — so it is a safe default everywhere.

## Install

Every tool in this repo — the converter, the DCP fetcher and the test
harness in [testing/](testing/README.md) — can be used **three ways**;
pick whichever fits your system. (Paths with spaces are shown
backslash-escaped throughout — exactly what bash/zsh tab completion
produces, no quoting needed.)

**1. Natively** — any Linux with Python ≥ 3.9 (validated on stock Ubuntu):

```sh
sudo apt install innoextract python3 python3.14-venv
python3 -m venv ~/.venvs/camicc && ~/.venvs/camicc/bin/pip install .
export PATH=~/.venvs/camicc/bin:$PATH   # provides camicc, camicc-fetch-dcps
```

**2. Nix** (flake):

```sh
nix run .# -- --help               # the converter
nix run .#fetch-dcps               # the DCP fetcher
nix develop                        # dev shell for the testing scripts
```

**3. Docker** (no Python or Nix on the host — the images are built by Nix
internally, so they contain exactly the versions pinned by `flake.lock`):

```sh
docker build -t camicc .
# converter — same arguments as the CLI, current directory mounted at /work:
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" camicc \
    Canon\ EOS\ RP\ Camera\ Standard
# DCP fetcher:
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
    --entrypoint /fetch/bin/camicc-fetch-dcps camicc
```

The `.icc` files are written to an `icc/` folder in the mounted
directory (`--install` is not useful inside the container — copy the
ICCs to `~/.config/darktable/color/in/` yourself). In Docker, file arguments are
resolved inside the container: anything under the current directory works
as-is; files elsewhere need their own mount (e.g.
`-v /path/to/my-dcps:/dcps:ro` and pass `/dcps/...`). The containerized
test harness is a separate image: see
[testing/README.md](testing/README.md).

## Usage

The workflow is: **get the DCPs → run camicc → the ICC lands in
darktable's profile folder → select it in darktable.**

### Step 1 — get DCP files for your camera

The easiest way is the bundled fetcher: it downloads the official Adobe
DNG Converter from adobe.com (~1.8 GB) and extracts **all** of Adobe's
camera profiles — including the "Camera Standard/Portrait/…" replicas of
each vendor's picture styles — into a local `dcps/` folder. Nothing is
installed or executed; the installer archive is simply unpacked:

```sh
# native (needs innoextract from your distro):
camicc-fetch-dcps                  # -> ./dcps/Camera/<model>/*.dcp
# Nix:
nix run .#fetch-dcps
# Docker:
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
    --entrypoint /fetch/bin/camicc-fetch-dcps camicc
```

The extracted profiles are copyrighted by Adobe: they are for your own use
and must not be committed or redistributed (the `dcps/` folder is
gitignored). Alternative sources — any `.dcp` from anywhere on disk works
too, e.g. RawTherapee's bundled profiles in
`/usr/share/rawtherapee/dcpprofiles/`, or profiles you made yourself.

### Step 2 — convert (and install)

With the default `dcps/` folder populated, a bare profile name is enough —
camicc looks it up automatically (`$CAMICC_DCP_DIR` overrides the search
locations; `~/.cache/camicc/dcps` is also tried):

Profile names may contain **wildcards**, matched against the default DCP
folders — the escaped `\*` below installs every Camera-style profile of
the model at once (6 DCPs: Standard/Portrait/Landscape/Neutral/Faithful/
Monochrome, each as *(camera look)* + *(colors only)*). The `*` must be
escaped or quoted, otherwise the shell globs it against your current
directory first — zsh even aborts with "no matches found":

```sh
# native — convert AND copy into darktable's profile folder
# (~/.config/darktable/color/in/) in one step:
camicc --install Canon\ EOS\ RP\ Camera\ \*
# Nix:
nix run .# -- --install Canon\ EOS\ RP\ Camera\ \*
# Docker (no --install: the container cannot see your darktable config;
# the ICCs land in ./icc, copy them yourself):
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" camicc \
    Canon\ EOS\ RP\ Camera\ \*
```

A single profile installs the same way, no wildcard needed:

```sh
camicc --install Canon\ EOS\ RP\ Camera\ Standard
```

Explicit paths work the same, several at once too:

```sh
camicc --install /usr/share/rawtherapee/dcpprofiles/Canon\ EOS\ RP.dcp
camicc --install dcps/Camera/Canon\ EOS\ RP/*.dcp
```

With **no profile argument at all**, every DCP found in the default
folders is converted. Point `$CAMICC_DCP_DIR` at one camera's folder to
convert (and `--install`) its complete profile set in one go:

```sh
CAMICC_DCP_DIR=dcps/Camera/Canon\ EOS\ RP camicc --install
```

(Without the scoping variable this converts the entire ~4,400-profile
tree — hours of work and an unusably long profile list in darktable.)

The generated `.icc` files always land in a local `icc/` folder (or
`-o <dir>`); `--install` additionally copies them into darktable's
profile folder. Without it, copy them manually:

```sh
camicc Canon\ EOS\ RP\ Camera\ Standard
mkdir -p ~/.config/darktable/color/in
cp icc/*.icc ~/.config/darktable/color/in/
```

For **Flatpak** darktable the profile folder is
`~/.var/app/org.darktable.Darktable/config/darktable/color/in/` — use `-o`
and copy there yourself.

Each DCP produces three ICCs, named after the camera and profile name
embedded in the DCP, e.g.:

```
Canon EOS RP Camera Standard (camera look).icc
Canon EOS RP Camera Standard (colors only).icc
Canon EOS RP Camera Standard (colors only, headroom).icc
```

- **`(camera look)`** — color tables **and** the DCP tone curve baked in,
  applied per RGB channel exactly like the camera/Adobe pipeline. This is the
  faithful "camera JPEG" rendering.
- **`(colors only)`** — color tables only, no tone curve. Use this if you
  want the camera's color character but prefer darktable's scene-referred
  tone mappers (sigmoid / filmic / agx).
- **`(colors only, headroom)`** — *(colors only)* with 2.7 EV of highlight
  headroom baked in, so bright clipped highlights survive the LUT (see
  [Highlight headroom](#highlight-headroom-the-headroom-variant)). Needs the
  exposure setup that the [darktable style](#darktable-styles-one-click-setup)
  applies for you.

Useful flags: `--variant look|colors|headroom|both|all` (default: `all`;
`both` = look+colors, the pre-headroom set), `--curve-mode channel|luminance`,
`--cct <kelvin>` (interpolate dual-illuminant DCPs at a shot color
temperature, e.g. a `@3200K` profile for tungsten light),
`--hsm-illuminant 1|2` (tungsten/daylight table for dual-illuminant DCPs),
`--custom-curve curve.json` (fit your own tone curve), `--grid N`,
`--name "My name"` (override the profile name from the DCP).

### Step 3 — select it in darktable

1. **Restart darktable** — the profile folder is only scanned at startup.
2. Open your raw in the darkroom and set **input color profile → profile** to
   the new entry (profiles appear under the name shown above).

### Step 4 — module settings that make or break it

For the **`(camera look)`** profiles the tone curve is already baked in, so
darktable's own tone mapping must be off or it is applied twice:

- disable **sigmoid** (or **filmic rgb** / **agx**) and **base curve**;
- set **exposure** to 0 EV (darktable's default preset adds ≈ +0.5–0.7 EV);
- set **color calibration** → adaptation to *none (bypass)* and use the
  legacy **white balance** module set to *as shot* — the profile expects
  fully white-balanced camera RGB, like RawTherapee feeds a DCP;
- optionally enable **lens correction** (the camera JPEG is
  vignette-corrected).

For the **`(colors only)`** profiles, keep your scene-referred tone mapper
(sigmoid / filmic / agx) — but the **white balance requirement is the
same as above**: legacy **white balance** module at *as shot* doing the
full balancing, **color calibration** adaptation at *none (bypass)*.
**All** profile variants expect fully white-balanced camera RGB at their
input; the modern workflow's split (white balance at "camera reference
(D65)" + color calibration doing the adaptation) feeds the profile wrong
values and shifts every color.

For the **`(colors only, headroom)`** profile the white-balance rule is the
same again, plus the exposure split that gives it the headroom:

- **exposure** at **−2 EV** (before the input profile);
- a **second exposure instance**, or a **basic adjustments** module, at
  **+2.7 EV** *after* the input profile — this restores the level so the net
  is the usual +0.7 EV, but the profile's LUT never receives a value it would
  clip. The [darktable style](#darktable-styles-one-click-setup) sets this up
  in one click (it uses *basic adjustments* for the +2.7 EV, since that
  module already sits after the input profile).

Do **not** try to use **color calibration** for that +2.7 EV gain: even with
a neutral matrix it re-derives the illuminant from the image and re-adapts
the (already balanced) white balance, casting the whole frame. Keep color
calibration bypassed and use exposure / basic adjustments for the gain.

## darktable styles (one-click setup)

Getting the headroom exposure split, the right tone mapper setting and the
disabled competing modules in place by hand is fiddly, so `camicc-styles`
generates a darktable **style** that does all of it — for the headroom
variant — in one click:

```sh
# 1. build and install the headroom ICCs (restart darktable afterwards):
camicc --install --variant headroom Canon\ EOS\ RP\ Camera\ \*
# 2. generate the matching styles into ./styles :
camicc-styles Canon\ EOS\ RP\ Camera\ \*        # Nix: nix run .#styles -- ...
```

Each `.dtstyle` sets the input color profile to the headroom ICC, the
−2 EV / +2.7 EV exposure split, **sigmoid** at the sweep-optimal
contrast 1.95 / skew −0.225 (override with `--contrast` / `--skew`), and
switches off filmic/base curve and color calibration so nothing fights the
profile. Import it in darktable's **lighttable → styles** panel (or
double-click the file), then apply it to any raw of that camera.

White balance is deliberately **not** in the style (the "as shot"
multipliers are per-image). Set it once in darktable's preferences —
**processing → auto-apply pixel workflow defaults → display-referred
(legacy)**, or **chromatic adaptation → legacy** — so the white balance
module carries the full "as shot" balance the profile expects. The exposure
module starts at −2 EV by design; brighten or darken from there as usual.
Lens correction is left out (it is geometry, not color) — enable it yourself
if you want the vignette/distortion correction the camera JPEG has.

## Where to get DCP profiles

- **`camicc-fetch-dcps`** (see Step 1) — downloads the free Adobe DNG
  Converter and extracts its complete profile set (~4,400 DCPs for
  essentially every camera Adobe supports, including the
  "Camera Standard/Portrait/Landscape/…" picture-style replicas) into
  `dcps/`, without installing or running anything.
- **RawTherapee installs** ship high-quality DCPs:
  `share/rawtherapee/dcpprofiles/`.
- Any DCP you made yourself (e.g. with dcamprof + a color target) works too.

This is camera-agnostic: any DCP with a ForwardMatrix converts directly, and
for older profiles that only carry a ColorMatrix (e.g. RawTherapee's Canon
EOS 5D profile) the forward matrix is derived automatically by inverting the
color matrix and Bradford-adapting the calibration illuminant to D50.

## Limitations

- A static ICC cannot re-interpolate DNG's dual-illuminant tables per shot
  the way Lightroom does. By default the **daylight** tables are used
  (`--hsm-illuminant`); with `--cct <kelvin>` the matrices and HueSatMap are
  interpolated at a chosen shot color temperature (e.g. `--cct 3200` for a
  tungsten-light profile, named `… @3200K`). How much this matters depends
  on the DCP: Adobe's **"Camera \*"** profiles are usually
  illuminant-invariant (identical forward matrices, no HueSatMap — their
  look is all in the LookTable/curve), so `--cct` changes nothing and says
  so. **"Adobe Standard"** and **RawTherapee-style** profiles carry dual
  HueSatMaps, where the tungsten tables differ substantially (mean ΔE ≈ 9
  and ≈ 16 respectively on the Canon EOS RP profiles) — for warm-light
  shots with those profiles a `--cct` build is the right choice.
- ICC LUT input profiles clamp the unbounded pipeline in darktable at
  diffuse white; extreme highlight-recovery workflows behave slightly
  differently than with matrix profiles. The **`(colors only, headroom)`**
  variant plus its [darktable style](#darktable-styles-one-click-setup)
  works around this for the common case (up to 2.7 EV / 6.5× above white) by
  splitting the exposure around the input profile; beyond that range the
  clamp still applies.
- RawTherapee's *auto-matched tone curve* is fitted per image and cannot
  be a static profile. `(camera look)` uses the DCP's own curve instead —
  for Adobe's "Camera *" profiles that is precisely the vendor look.

## License

GPL-3.0-or-later for all code in this repository.

### Profile licensing notes (not legal advice)

camicc is original code implementing Adobe's openly published DNG
specification; it contains no Adobe code or data. The camera profiles it
converts have their own owners, so the project draws a deliberate line:

- **Nothing Adobe-derived is redistributed.** Adobe's DCPs and any ICC
  converted from them stay on your machine: `camicc-fetch-dcps` downloads
  Adobe DNG Converter from Adobe's own servers at runtime and extracts it
  locally (the same long-established route documented by
  [RawPedia](https://rawpedia.rawtherapee.com/How_to_get_LCP_and_DCP_profiles)
  and packaged e.g. in the
  [AUR](https://aur.archlinux.org/packages/adobe-dng-dcp)); `dcps/` and
  `*.icc` are gitignored, and the committed test folder carries only a
  sha256 of the profile it used, never the profile.
- **Local conversion for processing your own photos** is what these
  profiles are for. Adobe tags its profiles with an embed policy;
  the ones handled here carry
  ["allow copying"](https://docs.rs/dng/latest/dng/tags/ifd/constant.ProfileEmbedPolicy.html),
  which permits copying them onto your system and using them to process
  any file. Photos you develop with a converted profile are yours.
- **Redistributing or selling converted profiles is the gray zone** — a
  converted ICC embeds Adobe's color tables in another container, which
  may constitute a derivative work of that data. This project doesn't do
  it and its tooling never does it for you. If you plan to publish
  profile packs converted from Adobe (or any third-party) DCPs, get
  proper legal advice or written permission first; profiles you convert
  from your own measurements or from freely licensed DCPs (e.g.
  RawTherapee's community-made ones, GPL) don't have this problem.
