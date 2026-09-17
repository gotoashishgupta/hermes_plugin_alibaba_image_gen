# Prompt Craft

How to write the image prompt that `generate_idea_image` submits. These principles are
model-agnostic: every modern T2I model rewards them, because they encode what all of them
are actually good and bad at.

## Contents
1. [Core principle](#core-principle)
2. [Prompt structure](#prompt-structure)
3. [Length](#length)
4. [Mode templates](#mode-templates)
5. [Text in images](#text-in-images)
6. [Composition](#composition)
7. [Plausibility](#plausibility)
8. [Pitfalls](#pitfalls)
9. [Model notes](#model-notes)

## Core principle

**Describe the scene in sentences. Never list keywords.**

A keyword list gives the model a bag of unrelated concepts and lets it choose the
arrangement. A sentence gives it a scene it can render.

- ❌ `woman, business suit, office, professional`
- ✅ `A professional businesswoman in a navy suit stands confidently in a modern
  glass-walled office overlooking a city skyline. Warm afternoon light streams
  through the windows.`

Write it as if briefing a photographer who has never seen your subject and cannot ask
follow-up questions.

## Prompt structure

```
A [SHOT TYPE] of [SUBJECT] [ACTION] in [ENVIRONMENT].
[LIGHTING] creates [MOOD]. [CAMERA/STYLE DETAILS]. [ASPECT RATIO].
```

Six components, in rough order of how much they matter:

| Component | Answers | Example |
|---|---|---|
| Subject & action | who/what, doing what | `elderly fisherman mending a net` |
| Setting | where | `seated on a wooden dock, misty harbour` |
| Lighting | what light, what quality | `soft golden light from the rising sun` |
| Mood | emotional tone | `contemplative and peaceful` |
| Technical | camera, lens, medium | `85mm f/2.0, shallow depth of field` |
| Format | shape | `4:5 portrait` |

Put the critical elements **early**. Later clauses get less attention, so a subject
mentioned in passing at the end is frequently dropped.

## Length

- **10–20 words** — minimal control, fast, high variance.
- **50–100 words** — the sweet spot. Enough specificity to steer, short enough that
  nothing gets diluted.
- **100–200 words** — for genuinely complex scenes with multiple subjects or precise
  layout. Past ~200 words models start dropping middle clauses.

When an image misses, the fix is usually **more specific**, not **longer**.

## Mode templates

Pick the mode from what the user asked for.

### Photography
```
Photorealistic [SHOT TYPE] of [SUBJECT], [ACTION], in [ENVIRONMENT].
Illuminated by [LIGHTING], creating [MOOD]. Captured with [CAMERA/LENS],
emphasizing [DETAILS]. [ASPECT RATIO] format.
```

Lenses: `35mm`/`50mm` standard · `85mm` portraits · `10–24mm` wide · `60–105mm` macro.
Lighting: golden hour (warm, soft) · blue hour (cool, atmospheric) · three-point
(studio) · dramatic side (high contrast) · Rembrandt (classic portrait).
Settings: shallow DOF `f/1.4–2.8` · deep focus `f/11–22` · motion blur for action.

### Art & illustration
```
[STYLE] illustration of [SUBJECT] [ACTION/POSE]. [COMPOSITION] with
[COLOR PALETTE]. [TECHNIQUE/MEDIUM]. Inspired by [REFERENCE],
emphasizing [QUALITIES].
```
Styles: watercolour · digital art · oil · pencil sketch · ink · anime/manga · pixel
art · minimalist vector.

### Product
```
High-resolution product photo of [PRODUCT] on [SURFACE]. [LIGHTING] to [PURPOSE].
Camera angle: [TYPE] to showcase [FEATURE]. Ultra-realistic, sharp focus on [DETAIL].
[ASPECT RATIO].
```

### Brand / marketing asset
```
[DESIGN TYPE] for [BRAND] featuring [SUBJECT]. [LAYOUT]. Brand palette
[COLORS]; typography [STYLE]. [MOOD] tone. [ASPECT RATIO].
```
When a brand reference image is supplied, describe only what the reference does *not*
already fix — the model sees the reference and will follow it.

### Abstract
```
[MOVEMENT] abstract exploring [CONCEPT]. [COMPOSITION] with [COLORS/TEXTURES].
[MEDIUM] emphasising [GOALS].
```

## Text in images

Text rendering is where most generations fail. Constraints that hold:

- **Under 25 characters total.** This is the hard limit; beyond it, expect corruption.
- **At most 2–3 phrases.**
- **Quote the exact string**: `"SUMMER"`, not `the word summer`.
- **Name the font style**: bold, script, sans-serif.
- **State the position**: `at top`, `centered`, `bottom right`.
- **State the colour**, and make sure it contrasts with its background.
- **Forbid extra text explicitly.** A character budget tells the model how much it *may*
  render, not that it should stop at your strings — models invent taglines, badges,
  dates, sub-headings and watermarks to fill space. Close the door in the prompt itself:
  `The only text in the image is "SUMMER SMASH" — no other words, letters, numbers,
  watermarks or signatures.` Without that sentence they routinely add copy nobody asked
  for (observed: ~40 characters of invented taglines on a poster that requested a single
  wordmark — and every eval assertion still passed, which is why this rule now exists).

```
Movie poster with bold red text "SUMMER" at top and "Adventure Awaits" in white
script font at bottom. Ocean sunset background. Cinematic style.
```

If a generation still garbles text after one retry, **stop fighting it**: generate the
image without text and overlay the type afterwards. Vector-crisp text is a compositing
job, not a diffusion job.

**For UI mockups, landing pages, dashboards and packaging with body copy, the layout
route is the default, not a fallback.** These are pages to be *read*, not pictures of a
page: they need exact spacing, a real type hierarchy and glyphs that hold at full size,
and a layout engine gives all three for free while diffusion gives approximate letters
and invented structure. Author them as HTML/CSS and rasterise it (headless Chrome at 2×
scale — see the compositing note in `pipeline.md`), then bring the render back through
stage 4 like any other candidate. Staying on diffusion is only defensible when the image
is a *depiction* of a device or a page rather than a page itself, and even then it must
carry no more than a phrase or two of type.

The reason this is stated so firmly: on a focus-app landing-page brief, a diffusion run
stayed inside the text budget and passed every assertion, and the user still rated it
"not good enough" while rating a headless-Chrome render of the same brief above it. The
gate and the assertion set both went green on the weaker image. Don't let the pipeline's
own rubric talk you out of the right tool.

**Read the text back, don't assume it.** After generating, actually read every string in
the image and compare it to the strings the brief asked for. Both failure modes are gate
failures: text that is *wrong* (garbled, misspelled, melted) and text that is *extra*
(invented taglines, badges, or a second brand's wordmark). The second is easy to miss
because the image can look great — check for it deliberately.

## Composition

Diffusion models default to centred, symmetric, flat compositions. Say otherwise when
you don't want that:

- `Rule-of-thirds composition places the horizon in the upper third`
- `Centred subject with symmetrical framing`
- `Leading lines draw the eye to the focal point`
- `Foreground, mid-ground and background layers`
- `Low vantage point looking up` / `slight overhead angle`

## Plausibility

Diffusion models render an impossible scene as fluently as a possible one, so plausibility
has to be *stated*, not assumed. Two things to state, every time.

**Scenes with a divider need the sides stated.** Any composition split by a threshold —
a net, a counter, a table, a doorway, a queue, a starting line — is where diffusion
models fail hardest, because they will happily put every subject on the same side of it.
State the relationship positionally and name both sides:

- ✅ `Two players leap on opposite sides of the net: the spiker left of the net, the
  blocker right of it, the net's mesh panel vertical between them.`
- ❌ `Two silhouetted players leap above a taut net.`

Name the divider's **orientation** too. A net described without one gets rendered as a
backdrop rather than a plane, which is how you end up with the divider behind the action
instead of through it. Same discipline for the ground: say what the subjects are standing
on or leaping over, or a beach scene will drift onto open water.

**State the physics you need, and only license the breakage you want.** The default
contract is a lawful world, so a prompt that stays silent on physics gets one — mostly.
Where a model tends to slip, say the law explicitly: `a single light source from the
upper left, every shadow falling down and to the right and consistent across all
objects`; `the mug rests on the table top, not intersecting it`; `still water reflects the
harbour and the boats above it`. If the brief *does* want a law broken — people floating,
a waterfall running upward, a shadow with no caster — you must say so outright, because
otherwise the renderer will either ignore the intent or break a law you wanted kept. Words
like "surreal" or "dreamlike" are **not** that instruction: they license a style, not
broken mechanics, and a model given "surreal" will often suspend gravity on its own.

**State the convention for any dial, gauge or progress bar.** A filled ring or bar is
ambiguous on its own — it can mean elapsed or remaining — and a model will render the arc
and the number it prints as two unrelated pictures. Say it in words:

- ✅ `A countdown ring that depletes as the session runs: the teal arc is the time
  remaining and the number inside it is that same value — 17:42 remaining of a 25:00
  session, so the teal arc covers about 71% of the ring and a pale track the rest.`
- ❌ `A soft teal progress ring reading 17:42.`

Better still, take the decision away from the model: a quantity that must match a printed
number is a **layout-route** job (see Composition), where the arc is computed from the
value rather than painted alongside it.

## Material and condition

Diffusion models default to the **pristine** version of everything: timber is freshly
milled, metal is new, a room is staged, produce is unblemished. So a brief's condition
adjectives are not decoration — they are requirements, and they are the ones that silently
get dropped. `weathered`, `rusty`, `worn`, `sun-bleached`, `scuffed`, `patinated`,
`hand-thrown`, `matte`, `crumpled`.

Say the condition **and describe the physical evidence of it**, because the adjective alone
is weak and the model will satisfy it with a tint:

- ✅ `A weathered dock railing: deep silvered grain, lengthwise splits and checking, grey
  lichen in the crevices, softened rounded arrises, sun-bleached to driftwood tones. Not
  new timber — the weather damage must be visible in the surface texture.`
- ❌ `A weathered wooden railing.`

Name what must **not** appear, since the default is the clean version — `not new timber`,
`no fresh saw marks`, `not polished`. A negative costs one clause and is the only reliable
way to push a model off its default. Then check the condition at the gate: if a brief asks
for weathered and the surface is smooth and uniform, that is the brief unmet, not a style
preference.

## Pitfalls

| Problem | Cause | Fix |
|---|---|---|
| Generic, stock-photo look | keyword listing | rewrite as sentences |
| Blurry / low quality | no technical spec | add camera, lens, `ultra-high resolution` |
| Wrong colours | vague colour words | name exact colours |
| Bad composition | no compositional guidance | add a rule from above |
| Missing elements | buried late in the prompt | move them to the front |
| Garbled text | too much text | cut to <25 chars, simplify font |
| Muddy style | conflicting instructions | pick one style; `photorealistic watercolour` is incoherent |
| Unwanted objects | ambiguous phrasing | explicitly state what must **not** appear |
| Impossible scene (opposing subjects on one side of a divider) | divider's sides and orientation unstated | state which subject is on which side, and the divider's plane |
| Subjects floating, or over the wrong ground | ground plane unstated | say what they stand on / leap over |
| Wrong count of limbs, players or products | no count given | state the count explicitly |
| Broken physics (shadows disagree, objects intersecting, reflections wrong, poured liquid defying gravity) | physics left unstated, so the model improvises | state the law you need (light direction, what rests on what); license breakage only when the brief asks for it |
| Unwanted floating / weightless subjects | "surreal", "dreamlike" or "magical" read as licence | say the world is lawful unless you mean otherwise; name the specific break you want |
| Dial, gauge or progress bar that disagrees with the number beside it | the arc and the number were described as two separate things | state the convention (arc = remaining *or* elapsed) and the same value for both; or move it to the layout route and compute the arc from the number |
| Quantity indicator whose meaning the viewer has to guess | convention never fixed by the design | label it ("remaining"), or compute the graphic from the value so the two cannot drift |

**Never mix incompatible styles.** Photography uses camera vocabulary; art uses
medium/technique vocabulary. Choose one register and stay in it.

## Model notes

The provider and model are chosen at generation time, so a prompt that works on one
model may need a small nudge on another. The entries below describe *families*, by way of
example — discover what the current runtime actually offers (`imagegen.py list --models`)
and read them as the kind of difference to expect between model families, not as a list
of supported models.

- **Nano Banana family** (`google/gemini-3.1-flash-image`, `-lite`, Pro) — strongest at
  conversational editing and multi-reference grounding. Accepts up to 14 reference
  images. Ratios: 14 exact values (`1:1 2:3 3:2 3:4 4:3 4:5 5:4 9:16 16:9 21:9 1:4 4:1 1:8 8:1`).
  Follows long, sentence-level prompts particularly well.
- **GPT Image** (`openai`, `openai/gpt-image-*`) — best at editing fidelity and literal
  instruction-following; `gpt-image-1-mini` is the one that does transparent cut-outs.
  More sensitive to keyword-style prompts than Gemini, but still prefers sentences.
- **FLUX / Seedream / Krea / MAI** (via OpenRouter) — strong aesthetics, lighter
  prompt-following. Be more explicit about layout and composition with these, and lean
  on reference images for brand fidelity.

Multi-turn editing (feed the previous image back as a reference and change one thing)
beats re-rolling from scratch — **one change per turn**. Asking for three edits at once
reliably produces a different image that honours none of them.
