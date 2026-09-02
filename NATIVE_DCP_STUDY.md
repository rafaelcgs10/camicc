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
  their correction by pixel brightness — but "brightness" is normalized to
  [0,1]. **Above white, behavior is undefined** = the super-white / unbounded
  problem; the reason the ICC route needs the headroom hack.

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
  - **A DCP's tone curve — and its tables' value axis — are display-referred
    assumptions.** That is Pascal's #41 objection. Dropping the DCP tone curve
    (the #42 plan) is right, but the table value-axis is display-referred too,
    so the separation is not fully clean — the residual is the research.

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
target).

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
  machinery to lean on).
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

## 8. What the EOS RP "Camera Standard" DCP actually does (measured)

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
