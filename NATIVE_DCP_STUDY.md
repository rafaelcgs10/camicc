# Native DCP in darktable — study & planning notes

> Working notes (uncommitted) capturing the conversation about making
> darktable apply DCP profiles **natively**, in a way that is sound with the
> scene-referred workflow. No code yet — the goal here is to fully understand
> the problem before solving it. Main target: a **POC native darktable
> module**.

---

## 1. Where this came from (the pixls.us thread)

Thread: *camicc — converting Adobe's DCP profiles to ICC for darktable*
(`https://discuss.pixls.us/t/.../59588`). The substantive technical exchange
is around posts #36–#45.

Key posts:

- **#36 Donatzsky** (RawTherapee dev) — linked starter material:
  - RawPedia — Color Management / DCP: `https://rawpedia.rawtherapee.com/Color_Management#DCP`
  - dcamprof — white balance: `https://rawtherapee.com/mirror/dcamprof/dcamprof.html#white_balance`
  - forum — "Camera input profiles in darktable": `https://discuss.pixls.us/t/camera-input-profiles-in-darktable/16596`
- **#37 ggbutcher** (Glenn Butcher) — *"great discussion on the Adobe
  workflow, including the role of the HueSatMap"*:
  `https://rawtherapee.com/mirror/dcamprof/camera-profiling.html#dng_profiles`
- **#38 MStraeten** (dt dev) — DCP support is absent only because *"no one
  implemented it… stuff isn't implemented if no one sees value in the
  effort."* → no hard architectural veto, just an effort/interest gap.
- **#40 mikae1** — the **dual-illuminant** capability is the genuinely
  valuable part.
- **#41 Pascal_Obry** (dt dev) — the core skepticism: *"as darktable is scene
  referred I'm not sure having a tone/hue correction based on curves makes
  sense. At which point in the pipeline should the tone curve be applied?
  Also a DCP profile contains a color matrix, when should it be applied."* →
  they aren't applied at the same time, so *"not sure it is easy or even if
  it is doable."*
- **#42 dark_photon (us)** — committed to the plan: *"try to come up with some
  POC implementation for DCPs in darktable. My initial idea, to keep the
  scene-referred workflow, is to **ignore the tone curve from the DCP**, so
  the user still makes that decision with sigmoid/agx… the **other data from
  the DCP could be used in the input color profile module**."*
- **#43 Pascal_Obry** — second constraint: *"You'll also need to deal with the
  **white balance** if you handle the profile. And again this is not on the
  same module. Let's see what you come up with!"* (an invitation).
- **#44 martinus** — floats "convert DCP → .dtstyle" as an alternative.
- **#45 dark_photon (us)** — *"styles with the correct DCP file for my camera
  in the input profile module, and some tweaked agx/sigmoid that approximates
  the tone curve from the DCP file."*
- **#46 mikae1** — agrees dropping the tone curve is right (*"Color is the
  interesting part"*); floats a future *"use DCP tone curve"* checkbox in
  `base curve` / **AgX** that picks up the DCP used earlier in the pipe.
  Reiterates dual-illuminant interpolation from two shot targets as "a dream
  come true"; the per-condition-ICC approach "was never a practical way of
  working."
- **#47 priort** — pipeline-ordering worry: the DCP needs a **CCT that would
  come from the Color Calibration module, which is *later* in the pipe** →
  "a lot of reworking… not break things." (The reverse-dependency form of
  Pascal's #43 WB concern. Our answer: CCT from the **as-shot neutral** in
  `commit_params`, not from CC — see §8.3/§8.7, sidesteps this.)
- **#48 finestructure** — independently arrives at the **double-adaptation**
  problem (§8.10–8.11): with a good input profile, color calibration "will
  have very little to do," so **CC's CAT should be disabled by default** or you
  white-balance twice. Note his instinct is **Design A** (DCP owns adaptation,
  CC off) — the opposite of our preferred **Design B**.
- **#49 ggbutcher** — the "is it even worth it?" challenge: camera has one
  spectral response but scene CCT varies wildly; wants to *measure* **how big
  the difference actually is between a tungsten scene developed with a StdA
  profile vs a D65 profile** (most software uses a D65 matrix for everything
  and our vision accommodates). Directly feeds P9/P10.
- **#50 MStraeten** — skeptic: finestructure's "CC has little to do" holds only
  if you shoot exclusively under the DCP's illuminant; and *"if you're able to
  generate a dcp for each shooting condition then you can also generate an icc
  for it which is already supported by darktable."* (Why-bother pushback; P9.)
- **#51 Christian (chris)** — **another dev already tried DCP in an
  experimental branch and "the results aren't encouraging,"** getting very good
  results instead from **DCamProf / Color-Calibration-+-chart ICCs** on a
  Pentax K-1. Suggests the advantage may be in the legacy workflow, toggling
  the built-in curve by workflow (cf. his POC unified tone-mapper selector,
  `https://discuss.pixls.us/t/poc-hybrid-workflow-unified-tone-mapper-selector/59006`).
  A concrete prior-art data point for validation (P10).
- **#52 dark_photon (us)** — "baby steps to understand the problem; will report
  when I have something concrete."
- **#53 priort** — **ART / RawTherapee are prior art for the exact tiered,
  component-selectable design:** checkboxes disable the non-linear DCP elements
  to keep essentially just the matrix for scene-referred, then tone-map later
  (log / CTL / etc.); or enable LookTable + tone curve + HueSat as wanted. He
  believes ART **partitions the DCP elements and applies each where most
  appropriate in the pipeline** — validates §8.3's tiers with per-component
  toggles; but if the "look" depends on the dropped tone curve, "that might not
  work with the DT pipeline as it is now" (P4 caveat).
- **#20 priort** — mentions **iccMAX** (richer container, spectral). A detour,
  not the road.

Also relevant, from the **"Camera input profiles in darktable" (16596)**
thread — the darktable-dev (Aurélien Pierre) skepticism: any RGB→XYZ LUT is
*"as bad as ICC"* unless it is a `camera RGB → spectral domain` LUT;
"ICC/DCP are container files." Understand this as the philosophical
resistance the POC will meet.

---

## 2. The one tension everything orbits around

A DCP is a **display-referred camera rendering description**. Its HueSatMap
value axis, its LookTable value axis, and its tone curve are all defined
**relative to a normalized white** (V ∈ [0,1]). darktable's scene-referred
workflow deliberately keeps data **linear and unbounded** and defers all tone
mapping to the very end (sigmoid / filmic / agx).

"Sound with scene-referred" therefore means answering precisely: **which
parts of the DCP are color (keep) vs tone (drop / make optional), and what
does the table's `V = 1` correspond to in scene-linear space when a highlight
is 8× white?** That last question is the actual research; the rest is
engineering.

### Two headline wins a native path gives us that the static ICC cannot
1. **Per-image dual-illuminant interpolation** (mikae1 #40 / Soupy #19) — a
   static ICC bakes one illuminant; native code recomputes the CCT blend per
   image from the as-shot white balance.
2. **Proper unbounded-highlight handling in float** — the ICC route goes
   through LittleCMS, which clamps to [0,1] and destroys highlights (hence the
   `headroom` variant + exposure-sandwich hack). Native float code needn't
   clamp, retiring the hack entirely.

### Pascal's two constraints, restated as our two hardest sub-problems
1. **"When is the color matrix applied vs the tone curve — they aren't at the
   same time."** Dropping the DCP tone curve (the #42 plan) sidesteps *half*
   of this — but the HueSatMap/LookTable carry a **value (V) axis** Adobe
   designed to sit *with* that display normalization. Feeding it
   scene-linear, unbounded V is the real soundness question (the successor to
   the headroom hack).
2. **"You'll need to deal with white balance — not on the same module."**
   `colorin` sits after WB in the pipe, and the DCP forward matrix wants
   *fully white-balanced camera RGB* — but darktable's modern workflow splits
   WB (D65 reference + color calibration doing the CAT). A native transform
   must either require legacy WB, or internally consume the as-shot neutral.
   (This is exactly camicc's existing "legacy WB, bypass color calibration"
   rule, now surfacing at the module level.)

### Two workstreams (complementary)
- **(a) POC native module** (#42) — the research: dual-illuminant per-image +
  proper unbounded highlights. The genuine win over camicc. **Main target.**
- **(b) DCP-approximating styles** (#45, martinus #44) — achievable *now* with
  camicc (colors-only ICC + tuned sigmoid/agx style). Lower-risk, ships value
  immediately, doubles as the validation baseline for (a).

---

## 3. Vocabulary primer (plain-language)

The correct DCP pipeline order (as implemented in `camicc/pipeline.py`, which
is authoritative — trust it over loose prose in the docs):

```
white-balanced camera RGB → ForwardMatrix → XYZ → ProPhoto → HSV
→ HueSatMap → LookTable → tone curve → output
```

**Color spaces (the "where are we" terms)**
- **Linear camera RGB** — the raw sensor's three channels; value ∝ photons;
  device-specific. *This is what enters the DCP pipeline.*
- **CIE XYZ** — the master reference space defined by human vision; the
  "ground truth" of what a color looks like; everything converts through it.
  **Y** = luminance (brightness); the rest is chromaticity (color independent
  of brightness).
- **Working RGB space / primaries** — an RGB space = a triangle of 3 primaries
  + a white point, expressible as a 3×3 matrix to/from XYZ. **ProPhoto**
  (RIMM) is the wide space the DNG tables work in; **Rec2020** is darktable's
  internal working space. *The module has to bridge ProPhoto ↔ Rec2020.*
- **White point / D50 / D65 / StdA** — the XYZ of "white": **StdA** ≈ 2856 K
  (tungsten), **D50** ≈ 5000 K, **D65** ≈ 6500 K (daylight).

**White balance & illuminant**
- **Illuminant / CCT** — the light, summarized by **correlated color
  temperature** (K). Warm/tungsten ≈ 3000 K, daylight ≈ 5500–6500 K.
- **White balance** — scaling raw R,G,B so gray comes out neutral. The
  **as-shot neutral** = the camera's chosen multipliers. *dcamprof: the DCP
  math assumes WB is already done — the pipeline feeds the profile a
  white-balanced image; a DCP cannot itself change white balance.*
- **Chromatic adaptation (CAT) / Bradford** — math modeling how the eye keeps
  colors constant under different lights (predict D65 color → D50). Bradford
  is the standard matrix method. *In darktable's modern workflow the CAT is
  done by the "color calibration" module, separate from the input profile —
  the root of Pascal's WB warning.*

**The two matrices (commonly confused)**
- **ColorMatrix** — XYZ → camera RGB. Job: **illuminant estimation** (from the
  as-shot neutral, guess the scene CCT). Does **not** render color. (camicc
  uses it only in `estimate_cct` + as a forward-matrix fallback.)
- **ForwardMatrix** — white-balanced camera RGB → XYZ. **The actual
  color-rendering matrix.** Torger: *"the actual color rendering is decided by
  the ForwardMatrix and the LUT, which both work on the white balanced
  image."*

**The 3D tables (the "camera look")**
- **HSV** — Hue (which color) / Saturation (how vivid) / Value (how bright);
  cheap from RGB; the DNG tables operate here.
- **HueSatMap (base table)** — a **"2.5D"** LUT indexed by (hue, sat,
  [value]) → (hue shift, sat scale, value scale). Torger: *"a rubber sheet
  stretched to reach appropriate positions."* Carries accuracy + camera
  character beyond the matrix.
- **LookTable** — same structure, applied *after* HueSatMap; carries the
  **subjective "look"** (Adobe's Camera Standard/Portrait/… character).
- **Value (V) axis** — ⭐ *most important for the POC.* Both tables can change
  their correction by pixel brightness — but "brightness" is expressed on a
  normalized [0,1] axis. **Above white, behavior is undefined** = the
  super-white / unbounded problem; the reason the ICC route needs the headroom
  hack. **Key refinement (see §8.9):** the V axis is only a *lookup coordinate*
  (an address into the table), not a container the pixel is squeezed into — so
  you clamp the *index*, never the pixel. And the two tables do **not** share
  the same V encoding: the base **HueSatMap is normally linear**, while the
  **LookTable is often sRGB-encoded** (display-referred). Each table's encoding
  is declared by a DNG tag (`ProfileHueSatMapEncoding` /
  `ProfileLookTableEncoding`).

**Tone & workflow philosophy**
- **Tone curve** — 1D brightness→brightness S-curve adding contrast; a
  **display-referred** aesthetic (the analog-film simulation). In DCP applied
  per-RGB-channel (also shifts hue/sat) or via a hue-preserving "neutral tone
  reproduction operator."
- **Scene-referred vs display-referred:**
  - *Scene-referred* (dt default): keep values ∝ real light, **linear and
    unbounded**, do color work there, apply **one** tone map at the very end.
  - *Display-referred* (older): bake a fixed tone curve **early** so values
    live in [0,1], then edit.
  - **A DCP's tone curve is a display-referred assumption; its tables' value
    axis is *partly* so.** That is Pascal's #41 objection. Dropping the DCP
    tone curve (the #42 plan) is right. **Refined (see §8.9):** the base
    **HueSatMap is linear** (its V axis is scene-linear-friendly — applying it
    to our data is the *intended* domain, modulo the anchor), whereas the
    **LookTable is often sRGB-encoded / display-referred** — that one really is
    out-of-domain in scene-linear, and is where the residual research (and the
    choice to drop it or round-trip it) lives.

**Gamut & clipping**
- **Gamut / out-of-gamut / negatives** — real camera colors can fall outside a
  working space → negative channels, which HSV & LUTs handle badly; needs a
  gamut-mapping strategy (see dcamprof's gamut-compression discussion).
- **Clipping to [0,1]** — ICC via LittleCMS clamps at the profile, destroying
  highlights. **Native float code needn't** — why native > ICC for
  highlights.
- **Dual-illuminant** — DCP stores two sets (matrices + HueSatMaps) for a warm
  and a cool illuminant; converter estimates CCT and blends. Static ICC can't
  do this per image; **native darktable could** — the headline advantage.

---

## 4. Reading list (in order) + what to extract

1. **RawPedia — Color Management, DCP section** — gentlest intro: what a DCP
   is, its 4 parts, why DCP clips less than ICC. Orientation only.
2. **dcamprof — `camera-profiling.html`** (ggbutcher's rec; conceptual
   backbone) — the *why* of HueSatMap/LookTable; the "raw converters are
   analog-camera simulators" framing; the **neutral tone reproduction
   operator**; the idealized scene-referred → appearance-model → creative
   "should-vs-is" workflow passage.
3. **dcamprof — `dcamprof.html`, white-balance + matrix sections** — nail
   **ColorMatrix vs ForwardMatrix**, "the profile expects white-balanced
   RGB," chromatic adaptation, **dual-illuminant interpolation** (how CCT
   drives the blend). Maps directly to Pascal's WB concern.
4. **darktable forum — "Camera input profiles in darktable" (16596)** — how
   dt's input-profile module behaves + the dev skepticism ("LUTs are as bad as
   ICC unless spectral"). Understand the pushback.
5. **Re-read `camicc/pipeline.py`** with this vocabulary in hand — it *is*
   these concepts as ~370 lines of code (`forward_matrix_from_color_matrix`,
   `apply_table`, `dcp_cct_weight` / `estimate_cct`, curve handling).

**Not in the thread but essential for the "soundness" half:** a primer on
darktable's **scene-referred workflow** (dt docs / Aurélien Pierre's
write-ups). Needed to judge what "sound" means. *(TODO: pull a good source.)*

Deeper/authoritative, for later: the **Adobe DNG 1.7 spec** — "Mapping Camera
Color Space to CIE XYZ" + profile tags — and **RawTherapee `rtengine/dcp.cc`**
(the closest existing native C++ implementation; our porting/reference
target). **ART** is a second reference and closer to our design intent (per-
component DCP toggles; forum #53).

**darktable's own dev docs** (`~/Documents/darktable/dev-doc/`, mirrored on
GitHub `darktable-org/darktable/tree/master/dev-doc`) — read *before* the
source, they give intent not just mechanics. The four that matter here, and why:
`pixelpipe_architecture.md` (the two-pass ROI engine, the hash cache, the
commit-order asymmetry — the last is our Tier-C/P6 hazard), `IOP_Module_API.md`
(the `params_t` vs `data_t` split = where per-image DCP work goes:
`commit_params` vs `process`), `maths.md` (**Design B's toolbox** — transposed
matrices, D50↔D65 helpers, CAT16/Bradford; see §8.12), and `New_Module_Guide.md`
(only if we make a *separate* IOP rather than extending `colorin`).

---

## 5. Five questions to answer while reading (the POC's real questions)

1. **What input does the DCP color math assume, and where in darktable is that
   produced?** (→ fully white-balanced camera RGB; which modules, set how.)
2. **What does the tables' value axis mean, and what should happen above
   white?** (Successor to the headroom hack.)
3. **Which DCP components are "color" (keep) vs "tone/look" (drop / optional)?**
4. **How does dual-illuminant interpolation get its CCT — and can darktable
   feed it per image?** (Headline advantage over ICC.)
5. **Where in darktable's pipe does each piece belong** — white-balance
   module, input color profile, color calibration (CAT), final tone mapper?
   (Pascal's "not on the same module," made concrete.)

---

## 6. darktable integration surface (for when we get there — no code yet)

- **IOP module system** — params/introspection, `commit_params`,
  `process` / `process_cl`, gui. Read `src/iop/colorin.c` (input color
  profile; where matrix/LUT input transforms + working space live) and
  `src/iop/lut3d.c` (dt already has CPU+GPU tetrahedral/trilinear 3D-LUT
  machinery to lean on). Orient with `dev-doc/IOP_Module_API.md` +
  `pixelpipe_architecture.md` first (see §4, §8.12).
- **Design-B color-science helpers already in-tree** (`dev-doc/maths.md`):
  `common/chromatic_adaptation.h` (CAT16 + Bradford, pre-transposed),
  `dt_XYZ_D50_2_XYZ_D65` / `dt_XYZ_D65_2_XYZ_D50`, `dt_colormatrix_mul` /
  `dt_apply_transposed_color_matrix`. `channelmixerrgb.c` is the worked CAT16
  example Design B reuses. (§8.12)
- **Pixelpipe / iop_order** — where `colorin` sits; scene-referred vs
  display-referred default presets; the modern WB split (white balance @
  camera-reference D65 + `channelmixerrgb` "color calibration" doing the CAT)
  vs legacy WB.
- **OpenCL parity** — required for upstreaming.
- **Open decision (deferred):** end target = upstreamable IOP vs local patch.
  Recommended sequencing: **prototype the scene-referred color science in the
  existing Python harness first** (fast iteration against Adobe ground truth),
  then port the settled algorithm into a dt IOP.

---

## 7. Validation (reuse what exists)

- Ground truth unchanged: **Lightroom/ACR export with the same DCP** is the
  fair benchmark; RawTherapee native as a second anchor; camera JPEG as a
  loose sanity check.
- The `testing/` harness (compare / suite / sweep, pinned Docker reference) is
  reusable — add a "darktable + native DCP" render path next to the ICC ones.
- Keep the CONTINUATION.md policy: the Docker testing image is the fixed
  reference; absolute scores are only comparable within one build.

---

## 8. Code-grounded findings (darktable master, explored 2026-08-04)

> Everything below was read directly from a `darktable` master checkout
> (`~/Documents/darktable`). File:line references are against that tree; two
> load-bearing claims (colorin applies WB itself; colorin outputs Lab) were
> spot-verified by hand, the rest come from a three-way source exploration.
> This section replaces prose speculation with what the code actually does.

### 8.0 Bottom line

A **color-only DCP maps onto `colorin`'s existing matrix fast path almost
1:1** — *if* "color-only" means the **ForwardMatrix** (± dual-illuminant
blend). The DCP ForwardMatrix is `white-balanced camRGB → XYZ(D50)`, which is
exactly what `colorin`'s `cmatrix` already is. The complexity cliff is the
**HueSatMap/LookTable**: `colorin` has **no 3D-LUT machinery**, and the
reusable interpolators live in a *different* module (`lut3d.c`) operating on an
RGB cube, not Adobe's HSV "2.5D" table. Hence three tiers (§8.3) with a sharp
jump between the first and the rest.

### 8.1 The mechanism = the clamp argument, in code

`colorin`'s `process()` (`src/iop/colorin.c:1184`) dispatches to two worlds,
and this dispatch *is* the study's ICC-clamps-highlights point:

- **Matrix fast path** — chosen when the profile is a pure matrix-shaper
  (`dt_is_valid_colormatrix(d->cmatrix[0][0])`, `colorin.c:1227`). Runtime data
  in `dt_iop_colorin_data_t` (`colorin.c:97-116`): `cmatrix` (camRGB→XYZ),
  per-channel tone `lut[3][65536]` (`lut[c][0] == -1.0f` ⇒ linear), and
  `unbounded_coeffs[3][3]`. The unbound sub-path `_cmatrix_proper_simple`
  (`colorin.c:943`) does: WB coeffs → tone curves *with exponential
  extrapolation above 1.0* (`_apply_tone_curves` → `dt_iop_eval_exp`,
  `colorin.c:614`; `imageop_math.h:133`) → matrix → Lab, and **never clamps**.
  This is the highlight-safe path.
- **LittleCMS fallback** — chosen for any profile with a CLUT, i.e. exactly
  what a real DCP-as-ICC LUT profile is (`process_lcms2_proper`,
  `colorin.c:1136`). `cmsDoTransform` clamps internally, and the clipping
  variant explicitly calls `dt_vector_clip` to [0,1] (`colorin.c:1018`;
  `math.h:732`). **This is the highlight-destroying ICC path.**

So the native win is literally the difference between these two code paths; a
native DCP transform must stay on (or extend) the matrix side to keep
highlights. The matrix decision itself: `dt_colorspaces_get_matrix_from_input_profile`
returns non-zero when the profile is not a matrix-shaper or carries a CLUT
(`colorspaces.c:108`, checks `cmsIsMatrixShaper` + CLUT presence), which is how
DCP-derived ICCs fall into LittleCMS today.

### 8.2 Existing profile/matrix infrastructure (what we build on)

- **Profile types** `dt_colorspaces_color_profile_type_t` (`colorspaces.h:77-108`).
  Camera-matrix types: `EMBEDDED_MATRIX` (10, DNG `d65_color_matrix[9]`),
  `STANDARD_MATRIX` (11, rawspeed `adobe_XYZ_to_CAM[4][3]`),
  `ENHANCED_MATRIX` (12, darktable-profiled), `VENDOR_MATRIX` (13),
  `ALTERNATE_MATRIX` (14). ICC types: `FILE` (0), `EMBEDDED_ICC` (9).
- **Camera matrices** are stored as primaries+whitepoint in
  `dt_profiled_colormatrices[]` (`src/common/colormatrices.c`), or read from
  the image (`dt_image_t.adobe_XYZ_to_CAM[4][3]`, `d65_color_matrix[9]`,
  `image.h:320/353`) via LibRaw (`imageio_libraw.c:458`).
- **Pipeline matrix representation** `dt_iop_order_iccprofile_info_t`
  (`iop_profile.h:38-56`): `matrix_in`/`matrix_out` (+ transposed twins),
  `lut_in/out[3]`, `unbounded_coeffs_in/out`, `nonlinearlut`. `colorin` commits
  via `dt_ioppr_set_pipe_input_profile_info` (`colorin.c:1573`), which for
  matrix types copies `matrix_in`, inverts and transposes it
  (`iop_profile.c:940-949`). Fast-path helpers `dt_ioppr_rgb_matrix_to_xyz` /
  `_xyz_to_rgb_matrix` (`iop_profile.h:266-414`) already accept any
  `dt_colormatrix_t` — **the matrix plumbing is ready for a ForwardMatrix.**
- **DCP parsing / HueSatMap / dual-illuminant: entirely absent.** Tree-wide
  search found no `.dcp` parser, no `ForwardMatrix`/`ProfileHueSatMap`/
  `illuminant` tag handling, no dual `ColorMatrix1/2` blending. All new code.
  (DNG *writing* emits `ColorMatrix1`+`AsShotNeutral`, `imageio_dng.c:90-146`,
  but that's the opposite direction.)

### 8.3 Three tiers of "color-only"

**Orthogonality warning (don't conflate with §8.11's Designs).** The **Tiers
A/B/C** here answer *how much of the DCP we apply* (matrix → +tables →
+dual-illuminant). The **Designs A/B** in §8.11 answer *who owns the
white-balance adaptation* (DCP bakes it vs. CAT16 does it). Same letters,
independent axes — every Tier can be built under either Design. Keep them
separate in discussion.

- **Tier A — ForwardMatrix only** (drop tone curve *and* HueSatMap). Near
  trivial: set `cmatrix = ForwardMatrix`, mark curves linear
  (`lut[c][0]=-1.0f`), `nonlinearlut=FALSE`; existing unbound matrix path runs,
  no clamp, super-white free (linear matrix has no value axis). Barely beyond
  today's `STANDARD/ENHANCED_MATRIX`, so small color gain over camicc alone.
- **Tier B — + HueSatMap/LookTable** (drop only tone curve; the #42 plan).
  The real value, and the real work. Needs a **new stage inside `colorin`'s
  matrix path**, between the matrix multiply and `dt_RGB_to_Lab`, in ProPhoto-
  HSV (matching `camicc/pipeline.py` order). `colorin` has nothing for this;
  `lut3d.c` interpolators (`_correct_pixel_tetrahedral` `lut3d.c:300`,
  `_correct_pixel_trilinear` `lut3d.c:230`, + OpenCL
  `kernel_lut3d_tetrahedral/trilinear`) are reusable as *algorithm reference*
  only — they interpolate an RGB cube, not (hue,sat,value)→(hueShift,satScale,
  valScale). This is where Q2 (below) becomes genuine research.
- **Tier C — per-image dual-illuminant blend** (on top of A or B). Inputs
  already available in `commit_params` via `dt_dev_chroma_t`
  (`as_shot`, `D65coeffs`, `wb_coeffs`, `late_correction`; `develop.h:154`).
  Estimate CCT from as-shot neutral (port camicc `estimate_cct` /
  `dcp_cct_weight`), blend the two ForwardMatrices/HueSatMaps per image — the
  headline advantage a static ICC cannot match.

### 8.4 Pascal's two constraints, grounded

- **WB "not on the same module" — less of a blocker than it sounds.** In
  scene-referred, `temperature` uses the `D65_LATE` preset
  (`late_correction=TRUE`, `temperature.c:1162`) and does *not* fully
  white-balance early; `colorin` applies the residual itself:
  `coeffs = D65coeffs / as_shot` (**verified** `colorin.c:651-655`). So the
  ForwardMatrix's required input (fully white-balanced camRGB) is exactly the
  state inside `colorin` right after those coeffs. **The real catch is the
  CAT:** chromatic adaptation runs *after* `colorin`, in `channelmixerrgb`
  ("color calibration") at iop_order **28.5** vs `colorin` at **28.0**
  (`iop_order.c`). Adobe's ForwardMatrix outputs **D50-adapted** XYZ, so a
  native DCP transform must **not** re-adapt to the working illuminant (would
  double-adapt against color calibration); it emits XYZ in darktable's expected
  reference and leaves the CAT downstream. This reproduces camicc's existing
  "legacy WB, bypass color calibration" rule as a module-ordering decision.
- **Matrix vs tone curve "not at the same time".** Dropping the DCP tone curve
  (Tier B) removes half: matrix in `colorin`, tone map at the end (sigmoid/agx).
  Residual half = the HueSatMap/LookTable **value axis is itself display-
  referred** ⇒ clean for Tier A, only partially clean for Tier B. That residual
  is the one true open research question; the rest is engineering.

### 8.5 Where each DCP piece belongs (pipeline map)

| DCP component      | darktable home                                             | Notes |
|--------------------|------------------------------------------------------------|-------|
| ForwardMatrix      | `colorin` `cmatrix` (register `iop_profile.c:940-949`)     | matrix plumbing ready |
| Dual-illuminant    | `colorin` `commit_params`                                  | CCT from `as_shot`; port camicc |
| HueSatMap/LookTable| **new stage in `colorin`**, matrix→ before `dt_RGB_to_Lab` | no code; `lut3d.c` as reference |
| Tone curve         | **dropped** (user's sigmoid/agx)                           | the #42 plan |
| CAT → working illum| **left to `channelmixerrgb`** (28.5)                       | do *not* re-adapt in `colorin` |
| White balance      | `temperature` (3.0) + residual in `colorin` (651-655)      | legacy-WB pairing simplest for POC |

Note: `colorin` outputs **Lab** (**verified** `output_colorspace` →
`IOP_CS_LAB`, `colorin.c:168`), so a Tier-B HSV stage must sit *before* the
`camRGB→XYZ→Lab` conversion, i.e. operate in ProPhoto-HSV inside `colorin`.

### 8.6 Concrete integration points

- **Tier-A POC, possibly no new colorspace type:** feed the ForwardMatrix
  through `dt_colorspaces_create_xyzmatrix_profile` (`colorspaces.c:935`) — it
  builds a linear-TRC matrix profile from cam→XYZ primaries → flows the
  existing matrix path unchanged.
- **Proper path:** add `DT_COLORSPACE_DCP` (`colorspaces.h:77-108`), a
  `dt_colorspaces_create_dcp_profile()` factory beside
  `dt_colorspaces_create_darktable_profile()`, and a case in `commit_params`
  (`colorin.c:1328-1405`); register via `dt_ioppr_set_pipe_input_profile_info`.
- **Tier-B stage:** HSV table lookup inside `_cmatrix_proper_simple`
  (`colorin.c:943`) + an OpenCL twin in `basic.cl` (mirror `colorin_unbound`,
  `basic.cl:1315`), reusing `lut3d.c` tetrahedral math. OpenCL parity is
  required for upstreaming.
- **DCP file parsing** is new (TIFF/IFD + Adobe tags); RawTherapee
  `rtengine/dcp.cc` is the porting reference. **ART** is a second reference and
  arguably closer to our design intent: per the thread (#53) ART exposes the
  DCP as **selectable components** (checkboxes to disable the non-linear
  elements → matrix-only scene-referred, tone-map later; or enable
  LookTable/tone-curve/HueSat), i.e. it already *partitions the DCP and applies
  each piece where most appropriate in the pipeline* — the same tiered,
  per-component toggle model as §8.3. Study how ART splits and orders these.

### 8.7 Answers to §5's five questions

1. **Input the DCP color math assumes, and where produced?** Fully
   white-balanced camRGB — produced *inside* `colorin` via `D65coeffs/as_shot`
   (`colorin.c:651-655`) in the scene-referred `D65_LATE` path; legacy path has
   it earlier in `temperature`.
2. **Value axis meaning / above white?** Irrelevant for Tier A (linear matrix,
   unbounded path already extrapolates, no clamp). For Tier B it is the open
   research item — HueSatMap V∈[0,1] fed unbounded scene-linear values; needs a
   normalization decision (successor to camicc's headroom hack).
3. **Color vs tone/look?** Keep: ForwardMatrix (+ dual-illuminant), optionally
   HueSatMap/LookTable. Drop: DCP tone curve. (See §8.5.)
4. **Dual-illuminant CCT source, per-image feasible?** Yes — CCT from
   `dt_dev_chroma_t.as_shot` in `commit_params`; blend two ForwardMatrices/
   HueSatMaps per image. This is the headline advantage over static ICC.
5. **Where each piece belongs?** WB: `temperature`(3.0)+`colorin`; matrix &
   HueSatMap: `colorin`(28.0); CAT: `channelmixerrgb`(28.5); tone: final
   mapper. (Full table §8.5.)

### 8.8 Recommended sequencing

Prototype **Tier A + Tier C** first (small, self-contained `colorin` matrix +
per-image dual-illuminant change; natively highlight-safe; immediately beats
static-ICC's single-illuminant limitation) → shippable, upstreamable POC and a
validation baseline. Then tackle **Tier B (HueSatMap)** separately, since that
carries the unbounded value-axis research and the new HSV-3D-LUT code (CPU +
OpenCL). Keep the color science in camicc's Python harness for fast iteration
against Adobe ground truth, then port the settled algorithm into the IOP.

**Open decision to raise with Pascal early (changes module wiring):** require
the legacy early-WB workflow for the DCP path, or reconcile with the
D65-reference + color-calibration split? The code makes legacy simpler, but
upstream is moving toward the modern split.

### 8.9 The value-axis problem, resolved (findings from camicc's own code)

Grounded in `~/Documents/dcp2icc/camicc/pipeline.py` (`apply_table`, l.66), which
is authoritative for what the tables actually do.

**Reframe #1 — the V axis is an *address*, not a *container*.** A table takes
`(hue, sat, value)` in and returns a *correction* `(hueShift, satScale,
valScale)` that is then applied to the real pixel. The value is used only to
*pick which table entry to read*. So normalizing V to [0,1] is about choosing
the **lookup coordinate**, and highlights are preserved by **clamping the index,
never the pixel**. This is exactly what the code does for the linear case:

```python
venc = np.clip(v, 0, 1)          # index is clamped to [0,1]
...
v = np.clip(v * dvc, 0, None)    # OUTPUT pixel kept unbounded above
```

So the whole "normalize → transform → inverse" round-trip is unnecessary for
the linear table: you divide a *copy* of the brightness to get an address, you
never rescale the pixel.

**Reframe #2 — the two tables live in different domains.** The `srgb_enc` flag
in `apply_table` is the tell, and the call sites differ:

- **HueSatMap** — called `srgb_enc=False` (l.316): **linear** V axis, index
  clamped, output unbounded (`np.clip(v*dvc, 0, None)`). Applying it to our
  scene-linear data is the *intended* domain. Only tuning = the anchor (what
  brightness counts as V=1). **Minimal quality loss.**
- **LookTable** — called with `dcp.look_table_srgb` (l.322), commonly **True**:
  **sRGB-encoded / display-referred** V axis, and its output is bounded to
  [0,1] (`srgb_inv(np.clip(venc*dvc, 0, 1))`, l.89). Applying it *directly* to
  unbounded scene-linear data is genuinely **out-of-domain** — this is where
  "used in a way it wasn't meant to be" is a real concern, and it bites in the
  highlights.

Caveat: the encoding is declared *per table* by DNG tags
(`ProfileHueSatMapEncoding` / `ProfileLookTableEncoding`); camicc currently
hardcodes HueSatMap = linear, which is the common case but not guaranteed —
the native module should read the tags.

**Consequences for the POC — three options for the LookTable specifically:**

1. **Keep HueSatMap, drop LookTable *and* tone curve (recommended for a
   scene-referred POC).** HueSatMap carries the *accurate color* (calibration);
   the LookTable carries Adobe's *subjective look*, which is display-referred
   and which the user re-decides anyway with sigmoid/agx. Cleanest; loses
   Adobe's look, keeps color accuracy.
2. **Keep both; apply the LookTable via a round-trip** (encode to sRGB [0,1] →
   apply → decode). This is the legitimate use of the "normalize/inverse" idea,
   restricted to the table that actually needs it (LookTable), up to 1.0 with
   last-slice hold above. Faithful to Adobe's look, more complex, reintroduces
   a bounded step.
3. **Apply both in linear, accept the difference.** Simplest; LookTable look
   won't match ACR exactly.

**Anchor question, narrowed.** After all this, the only real free parameter for
the *HueSatMap* is the anchor "what scene-linear brightness = V=1?" — candidates
unchanged from §8.7 Q2: profile `BaselineExposure` (most faithful), sensor-clip
via `processed_maximum` (deterministic fallback), with index clamped + last
slice held above 1. Validate by exposure-sweep vs an ACR export: the right
anchor keeps highlight hue/sat stable as exposure is pushed.

### 8.10 The white-balance residual (`as_shot → D65`) and what a DCP needs instead

**What the residual is.** darktable stores two per-channel *camera-RGB gain
vectors* in `dt_dev_chroma_t` (develop.h:159-162):

- `as_shot` — gains that neutralize the **actual scene light** (from
  AsShotNeutral EXIF); apply them and a scene gray card → neutral.
- `D65coeffs` — gains that neutralize a **standard D65 daylight** reference for
  *this camera* (a sensor property, scene-independent).

In the modern "as shot to reference" preset (`D65_LATE`), `temperature` applies
`as_shot` early (data is *scene-neutral*, good for demosaic / highlight
reconstruction), and then `colorin` applies the residual
`D65coeffs / as_shot` (colorin.c:653-655). Net gain from raw:

```
as_shot × (D65coeffs / as_shot) = D65coeffs
```

so the data is re-expressed as if balanced to **D65** just before the matrix.
Why: darktable's built-in camera matrix (`adobe_XYZ_to_CAM`) is **D65-calibrated**
— it assumes D65-referenced camera RGB. The residual lands the neutral axis
where the matrix expects it. Splitting it (as_shot early, residual late) gives
scene-neutral data to the raw modules *and* D65 data to the matrix. **It is not
a chromatic adaptation** — just channel scaling; the real perceptual adaptation
is done later by color calibration (`channelmixerrgb`, 28.5) in LMS/CAT16.

**What a DCP needs instead (design answer).** A DCP ForwardMatrix is *defined*
to take **as-shot-balanced** camera RGB: Adobe white-balances by dividing by the
as-shot neutral (scene neutral → (1,1,1)) and builds the matrix so
`FM · (1,1,1) = D50 white`. So the FM's expected input is exactly the
as-shot-balanced data `temperature` already produces. Therefore the correct
residual for a DCP is **identity**:

```
X / as_shot  with  X = as_shot  ⇒  residual = 1   (do NOT push to D65)
```

Dimensional caveat: you can't substitute "D50" for `D65coeffs` in that ratio —
`as_shot`/`D65coeffs` are *camera-RGB gain vectors*, while D50/D65 are *XYZ white
points*. The camera-RGB analog of `D65coeffs` would be `D50coeffs` (gains
neutralizing D50), but we want **neither** — the FM wants as-shot balance, not a
re-reference to any fixed illuminant.

**Output side — the D50/D65 non-issue (corrected).** It is tempting to think the
FM's **D50 XYZ** output must be converted to D65 for downstream. It doesn't:
**darktable's connection space is D50.** `dt_XYZ_to_Lab` / `dt_Lab_to_XYZ`
explicitly "use D50 white point" (colorspaces_inline_conversions.h:42/77/87, D50
vector `{0.9642, 1.0, 0.8249}`), and its ICC-derived matrices are D50-PCS. So:

- `colorin` outputs **Lab, D50-referenced**; the FM's **D50 XYZ** matches the PCS
  directly → `dt_XYZ_to_Lab` with **no adaptation** at the colorin boundary.
- **D65 is only the native white of the working RGB spaces** (linear Rec2020).
  The D50↔D65 conversion between the D50 PCS and those spaces is **baked into the
  working-profile matrices** and applied by the generic inter-module color
  conversion (`dt_ioppr_transform_image_colorspace`) for *every* image — not
  something `colorin`, color calibration, or the DCP path adds. It's a fixed
  colorimetric bake, not a scene adaptation.

**So the real question is not white-point conversion — it's scene-illuminant
adaptation *ownership*.** These two paths differ philosophically:

- **DCP path removes the scene cast itself.** as-shot balance (neutral→(1,1,1))
  + `FM·(1,1,1)=D50 white` ⇒ a neutral under *any* light maps to D50 white →
  scene illuminant already adapted out (legacy-style).
- **darktable native path leaves the cast in.** `D65coeffs` balance does not
  neutralize non-D65 scenes, so `colorin` outputs XYZ *with* the cast, and
  **color calibration** (channelmixerrgb, CAT16) removes it later.

⇒ Dropping a DCP into `colorin` with color calibration **on** would
**double-adapt**. The fix is not a CAT — it's that **the DCP owns the adaptation,
so color calibration must be bypassed (identity)** for DCP images. This is
camicc's "legacy WB, bypass color calibration" rule, made precise:

This gives the **Design-A contract** (faithful-to-Adobe; DCP owns adaptation):

- DCP pre-conditioning: **as-shot balance (residual = 1)** — bypass `colorin`'s
  `late_correction` push-to-D65 for DCP profiles.
- DCP output: **D50 XYZ → D50 Lab directly** (PCS already D50; no extra CAT).
- **Color calibration OFF** for DCP images (the DCP already did the scene
  adaptation) — the one thing that must be enforced to avoid double-adaptation.

**But we do not prefer Design A — see §8.11. We prefer Design B, where color
calibration owns the adaptation and the DCP is stripped to characterization
only.**

### 8.11 Who owns the scene-illuminant adaptation — Design A vs Design B (we pick B)

"Adapting the scene illuminant" = the white-balance-as-chromatic-adaptation step:
removing the *color of the light* so a scene neutral renders neutral (the job
human vision does automatically; the sensor does not). Exactly **one** stage in
the pipe must do it — if two do, colors are double-corrected (a warm shot swings
cold).

**The catch: a DCP ForwardMatrix has the adaptation baked in.** It is not pure
color character; conceptually:

```
ForwardMatrix = [ chromatic adaptation: calibration-light → D50 (Bradford) ] × [ camera color characterization ]
```

So using the FM as-is *forces* the adaptation. Note the **dual-illuminant
interpolation is NOT adaptation** — picking the matrix for the scene CCT is part
of *characterization* (sensor metamerism differs per light); we always keep
that. Only the final Bradford-to-D50 is the "adaptation" in question.

**Design A — faithful to Adobe (DCP adapts, color calibration OFF).** DCP does
everything, adaptation baked to D50, exactly like ACR/Lightroom; this is the
§8.10 contract and what camicc's ICC output already does. Closest to Adobe's
look; least darktable-idiomatic.

**Design B — darktable-native (DCP characterizes, color calibration adapts). ←
PREFERRED.** Strip the baked Bradford out of the FM so it outputs XYZ
*referenced to the scene light* (neutral left at the scene chromaticity,
un-adapted); hand that to **color calibration**, which performs the adaptation
to the working reference with **CAT16**. Rationale for preferring B:

- Aligns with darktable's modern philosophy (adaptation done properly, late, in
  a perceptual space — Aurélien Pierre's argument), and directly answers
  Pascal's #41/#43 concern by putting the adaptation in its proper module.
- CAT16 is a better adaptation model than the legacy Bradford baked into the DCP
  (better on saturated colors / skin).
- Keeps the user's normal darktable controls (illuminant, CAT method) live.

**What Design B requires (open implementation items):**

- **Un-bake the FM.** `M_char ≈ Bradford(D50 → sceneWhite)⁻¹ · FM_interp`, so
  `M_char` maps (white-balanced) camera RGB → XYZ referenced to the scene
  illuminant. Needs the DCP's `CalibrationIlluminant1/2` white points; dcamprof
  does this decomposition, so the math is known.
- **Feed color calibration the *same* scene illuminant** the DCP CCT estimate
  used (from the as-shot neutral), so the two don't fight. darktable's color
  calibration already accepts a custom / "as shot in camera" illuminant, so this
  is natural.
- **Work out the WB pre-conditioning** (temperature coeffs / whether the residual
  is identity) that leaves the data in the state `M_char` expects — derive +
  validate; do not assume.
- **HueSatMap/LookTable domain re-opens (ties to §8.9).** The tables were tuned
  for the *adapted* rendering; applying them in the un-adapted characterization
  space is another domain question. Decide per table whether to apply in
  characterization space or after color calibration's adaptation.

**Cost of B (accepted):** it deviates from ACR's exact look (we're substituting
CAT16 for Adobe's baked adaptation), and it needs the un-baking math + white
points. The validation harness (§7) must therefore compare *color character*
fairly (factor out the different adaptation), not expect a pixel match to ACR.

**Forum corroboration & counter-pull (posts #47–#50).** The ownership question
was raised independently on the thread. **finestructure (#48)** reached the
double-adaptation insight cold — "with a good input profile color calibration
will have very little to do… CC's CAT should be disabled by default or it's
white balance twice" — but his instinct lands on **Design A** (DCP owns the
adaptation, CC off), the opposite of our pick. **MStraeten (#50)** narrows it
further: the "CC has little to do" claim only holds if you shoot exclusively
under the DCP's illuminant. Both are worth answering explicitly, because Design
B's whole point is to keep CC *on* (owning a better CAT16 adaptation) precisely
so the profile isn't tied to one shooting condition. **priort (#47)** flags the
ordering trap — if the CCT came *from* CC (28.5) it would be needed before CC
runs; our answer (CCT from the as-shot neutral in `commit_params`, §8.3/§8.7)
is what makes Design B orderable at all, and should be stated as the rebuttal.

**Net contract (Design B):** DCP → characterization-only matrix (dual-illuminant
CCT interpolation kept, Bradford-to-D50 removed) → **color calibration ON**,
adapting the as-shot illuminant to the working reference via CAT16. This
supersedes §8.10's "color calibration OFF" conclusion.

### 8.12 dev-doc grounding & the Design-B toolbox (explored 2026-08-05)

> darktable ships authoritative developer docs in `~/Documents/darktable/dev-doc/`
> (mirrored at `github.com/darktable-org/darktable/tree/master/dev-doc`). Read
> these *before* the source — they explain intent, not just mechanics. The four
> that matter: `pixelpipe_architecture.md`, `IOP_Module_API.md`, `maths.md`,
> `New_Module_Guide.md`. Key findings from reading them against the source:

**Finding 1 — Design B is much less scary than §8.11 implies: its color science
is already documented, reusable dt helpers.** `maths.md` + the headers it points
to give us the exact primitives:
- `common/chromatic_adaptation.h` ships **CAT16 *and* Bradford** matrices,
  pre-transposed, with the von-Kries LMS scaling spelled out. So "un-bake
  Adobe's baked *Bradford*-to-D50, re-adapt with *CAT16*" (§8.11) is assembled
  from existing helpers, not new math — **this materially de-risks P2.**
- `dt_XYZ_D50_2_XYZ_D65` / `dt_XYZ_D65_2_XYZ_D50` exist — confirms §8.10's
  "D50/D65 non-issue": dt's PCS is **D50**, Adobe's FM outputs **D50**, so the
  `colorin` boundary needs *no* adaptation.
- **Matrix convention caveat (real footgun):** every `dt_colormatrix_t` is stored
  **transposed** for SIMD (`dt_apply_transposed_color_matrix`,
  `dt_colormatrix_mul`), and `(A·B)^T = B^T·A^T`. Our un-baking algebra must be
  done in this transposed convention or it silently swaps channels. Study
  `channelmixerrgb.c` (color calibration) as the worked example of CAT16
  adaptation we are reusing — Design B *leans on* it rather than reinventing it.

**Finding 2 — the pipe's ordering asymmetry is the precise hazard for Tier C /
P6.** `pixelpipe_architecture.md` §"Pipeline Ordering Asymmetry":
`commit_params()` runs **forward** (temperature → colorin → channelmixerrgb),
but default-loading runs **reverse**. `temperature.c` *writes*
`dev->chroma.wb_coeffs`; `channelmixerrgb.c` *reads* it — the exact shared-state
channel our dual-illuminant CCT estimate (`dt_dev_chroma_t.as_shot`) travels
through. A documented bug already occurred here (stale values on reverse
iteration). ⇒ Tier C must treat `dev->chroma` as reset-sensitive shared state;
this is the concrete mechanism behind **P6** ("keeping three things consistent").

**Finding 3 — the hash cache is how per-image WB reactivity works (for free, if
we route through it).** Color-profile state is folded into the *base* cache hash
for every lookup (not per-piece), because profiles are committed globally. Our
per-image dual-illuminant blend depends on the as-shot neutral, so as long as it
flows through `commit_params`/the profile-info the cache invalidates correctly
when the user changes WB — but a blend value computed *outside* that path would
leave a stale cached buffer. Design note for Tier C, ties to **P6**.

**Finding 4 — `colorin` code anatomy confirms the plan targets one file
(re-verified 2026-08-05).** `dt_iop_colorin_data_t` (`colorin.c:97`) with
`cmatrix` (`:106`, the ForwardMatrix slot), `lut[3][]`+`unbounded_coeffs`
(`:109`, the never-clamp extrapolation), `nonlinearlut` (`:111`); the WB residual
`D65coeffs/as_shot` (`:653-655`); `default_colorspace → IOP_CS_LAB` (`:168`, why
a Tier-B HSV stage must sit *before* the Lab conversion); highlight-safe path
`_cmatrix_proper_simple` (`:943`) / `_cmatrix_fastpath_simple` (`:824`) vs the
clamping `_cmatrix_fastpath_clipping`+`dt_vector_clip` (`:854/:893`). Line numbers
drift slightly from §8.1's earlier read but the structure is unchanged.

**Finding 5 — Tier B's `lut3d.c` reuse is *topological*, not literal.** Its
interpolators take an **RGB cube** → RGB; Adobe's tables are **HSV "2.5D"** →
*correction deltas* (hueShift, satScale, valScale) with hue wrap-around. We port
the tetrahedral *weighting*, not the function. §8.9's "V is an address, clamp the
index not the pixel" is a statement about the *indexing* code we write around the
ported weights.

---

### 8.13 What the EOS RP "Camera Standard" DCP actually does (measured, 2026-09-02)

Numerical characterization (2026-09-02, `dcp_study.py` on the DCP tables via
camicc's own pipeline code) — input to the native-style fitting work:

**Structure**
- **No HueSatMap at all.** `ForwardMatrix1 == ForwardMatrix2`; the whole
  color character is one LookTable (90x16x16, sRGB-encoded value axis) plus
  a 128-point channel tone curve. Dual-illuminant interpolation is
  irrelevant for this profile.
- Baseline exposure offset -0.15 EV.

**LookTable (vs matrix-only baseline, median per Lab-hue bin)**
- Hue rotations are big and value-dependent: orange +17deg (V=0.05) ->
  +33deg (V=0.9); lavender +4deg -> +25deg; yellow reversed +31deg ->
  +17deg. Red, green, cyan, magenta are value-stable.
- Chroma: yellow/green/blue/lavender boosted (x1.1-1.4 mid), red/cyan cut
  (x0.86/x0.69); everything desaturates toward V=0.9.
- Saturation-dependent compression: chroma ratio falls as input saturation
  rises (cyan x0.95 at low sat -> x0.42 at high sat) — a soft
  gamut/saturation compressor, exactly what agx primaries insets model.

**Channel tone curve (applied per RGB channel in ProPhoto)**
- Neutral axis: mid-gray +1.12 EV, gentle log-log slope 1.07 at 0.18,
  crushing toe (-2.5 EV at 0.001), strong shoulder.
- Color side-effects vs a luminance-only curve: shadows chroma x1.6-1.9,
  highlights chroma x0.5, hue shifts +/-3-4deg — the "zone saturation" that
  colorbalancergb / agx look handle.

**Mapping to darktable modules** (what `testing/fitstyle.py` implements):
tone + global saturation/purity -> agx curve + agx primaries; per-hue
corrections -> color equalizer; the value-dependence of the hue rotations ->
a second color-equalizer instance parametric-masked to scene highlights;
zone saturation -> color balance rgb.

**Environment lesson (root cause of the yellow-skin complaint):** the v1
native style was fitted under the harness's legacy-WB display-referred conf
but used under the modern workflow (D65 WB + color calibration CAT). Same
params re-rendered under modern defaults score dE76 8.5 with skin rotated
+10deg toward yellow vs Lightroom. Any fit must run in the environment the
style is used in: modern workflow, WB and color calibration untouched.

---

## 9. Open problems to solve (still to debate)

The design questions that remain, grouped by kind. Each lists the **crux** to
decide. Assumes Design B (§8.11) as the working direction. Already-resolved
questions live in §8.7–§8.11 and are not repeated here.

**Suggested debate order:** P1 → P2 → P3 → P4 → P5–P8 (robustness) → P9–P10
(strategy). P1/P2 can invalidate Design B itself, so settle them first.

### Color-science correctness (can silently wreck results)

- **P1 — Table application space & point in the flow.** The tables are defined in
  **ProPhoto-HSV**; darktable works in Rec2020; PCS is D50 Lab; Design B leaves
  the data **un-adapted** until color calibration. Crux: do we apply
  HueSatMap/LookTable *before* the CAT16 adaptation (on un-adapted data) or
  *after*? The tables were tuned for the *adapted* rendering (§8.9), and Design B
  moves the adaptation downstream — so this must be decided before any table
  work. **Blocks Design B.**
- **P2 — Is "un-baking the ForwardMatrix" clean?** Design B depends on stripping
  the Bradford-to-D50 out of the FM to get a characterization-only matrix. Crux:
  is that decomposition unique/stable given `CameraCalibration`/`AnalogBalance`
  terms and the dual-illuminant interpolation space? If not, Design B's premise
  weakens. Verify against dcamprof's decomposition. **Partly de-risked (§8.12):**
  the *tooling* exists — `common/chromatic_adaptation.h` has CAT16 + Bradford
  pre-transposed, so the decomposition can be prototyped inside dt's own helpers;
  what's still open is whether it's *unique/stable*, not whether it's buildable.
  Watch the transposed-matrix convention (`(A·B)^T=B^T·A^T`). **Blocks Design B.**
- **P3 — Gamut & negative values before HSV.** HSV of an out-of-gamut color
  (negative channel after the matrix) is meaningless; worse under Design B
  because data stays un-adapted and unbounded. Crux: widen (work in ProPhoto),
  gamut-compress (dcamprof-style), or clip — and where in the flow.

### Tone & user experience

- **P4 — Tone/look promise.** We drop the DCP tone curve (and likely LookTable)
  and haven't decided `BaselineExposure`. Result: images look flat/dark vs ACR.
  Crux: accept the honest promise **"accurate color, your own tone"** (not "the
  Adobe look"), and decide whether to ship the styles workstream (#44/#45: DCP +
  tuned sigmoid/agx approximating the DCP curve, + BaselineExposure as a starting
  exposure offset) to recover the look for users who want it. Related forum
  ideas: **mikae1 (#46)** — a *"use DCP tone curve"* checkbox in `base curve` /
  AgX that picks up the DCP from earlier in the pipe (an in-pipeline recovery
  path, vs the styles workstream's out-of-band one). **priort (#53)** — the ART
  precedent (per-component checkboxes) is the clean UX for this, but warns the
  Adobe "look" can *depend* on the dropped tone curve, so a colors-only result
  is honestly a different rendering, not the ACR look minus contrast.
- **P5 — Profile selection & matching.** Adobe ships several looks per camera
  (Standard/Portrait/Landscape/Neutral/Faithful/…). Crux: how the user picks,
  how we match make/model to a `.dcp`, embedded vs external, and behaviour when
  no profile exists.

### Robustness / edge cases

- **P6 — Trusting the as-shot neutral; keeping three things consistent.** The DCP
  CCT estimate, darktable WB, and color calibration's illuminant all derive from
  the as-shot neutral (can be missing, "best guess", or user-overridden via
  spot/custom WB). Crux: single source of truth, and keeping all three
  consistent *and reactive* when the user changes WB. **Grounded (§8.12):** the
  shared channel is `dev->chroma`, which has a documented **forward-commit /
  reverse-default-load ordering asymmetry** — Tier C must treat it as
  reset-sensitive, and must route the blend through `commit_params`/profile-info
  so the base cache hash invalidates on WB change (else a stale buffer persists).
- **P7 — Matrix/table availability combinatorics.** DCPs vary: single- vs
  dual-illuminant, ForwardMatrix vs only ColorMatrix, HueSatMap/LookTable present
  or not, monochrome. Crux: enumerate the cases and the graceful fallback for
  each (e.g. no FM → derive from ColorMatrix; single illuminant → skip
  interpolation).
- **P8 — Highlight reconstruction interaction.** `highlights` (order 4.0, before
  colorin) invents color for clipped highlights, which then flows through the
  matrix, tables, and value-axis logic. Crux: confirm no bad interaction on
  near-clipped pixels (minor).

### Strategy

- **P9 — Upstream philosophy & the "is it even worth it?" challenge.** Two
  pushbacks now on record. (a) Aurélien Pierre's "LUTs are as bad as ICC unless
  spectral" objection. (b) The thread's own skepticism (**ggbutcher #49,
  MStraeten #50, chris #51**): most software uses one D65 matrix for everything
  and vision accommodates the difference, so does CCT-tailored rendering earn
  its keep? — and **#51 is a data point that it may not** (a dev's experimental
  DCP branch was "not encouraging" vs DCamProf / CC-+-chart ICCs). Crux: aim
  upstream leading with the Design-B framing (CAT16 adaptation +
  characterization matrix, *more* aligned with dt philosophy) vs prove locally
  first — and be ready to concede the honest scope if the win is marginal.
- **P10 — Validation under Design B (must answer #49 head-on).** Design B
  deliberately deviates from ACR (CAT16 vs baked Bradford, dropped tone), so a
  pixel diff vs a Lightroom export is unfair. Crux: a comparison that isolates
  **color character** (compare color-chart chromaticities, or normalize tone on
  both sides) so tests don't "fail" for non-errors. **Add ggbutcher's specific
  experiment (#49):** develop a tungsten-lit scene with a **StdA profile vs a
  D65 profile** and quantify the difference — this both answers the worth-it
  question (P9) and validates that the dual-illuminant machinery does something
  visible. Cross-check against **#51's finding** by including a DCamProf /
  CC-+-chart ICC as a baseline in the same harness. Reuse the §7 harness.
