# The quality-gated pipeline

Every image the skill delivers must clear this pipeline
(`brand_analysis → generate_design_concepts → generate_idea_image → evaluate_image`). Do
not skip a stage to save time: the pipeline exists because ungated generation ships
mediocre, off-brand images that the user has to reject by hand.

```
user brief (text) + optional reference image
        │
        ▼
1. brand_analysis ──────────────────► Visual Design DNA (JSON)
        │
        ▼
2. generate_design_concepts ────────► concepts[] {title, description, prompt}
        │
        ▼
3. generate_idea_image ─────────────► image file  (hg_image.py)
        │
        ▼
4. evaluate_image ─────────────────► scorecard {scores, overall, pass, feedback}
        │
        ├─ pass (overall ≥ 4.0) ───► deliver image + scorecard
        └─ fail  ──► fold feedback into the prompt, regenerate (≤ 2 retries)
```

## 1. brand_analysis — Visual Design DNA

Input: the user's brief text, plus the reference image if one was provided (path or URL).

Extract the brand's Visual Design DNA as a JSON object with exactly these keys:

```json
{
  "colors":        "primary/secondary/accent HEX or descriptions + the 'vibe' (e.g. warm earth, cool minimal)",
  "typography":    "font families, weights, hierarchy feel",
  "icon_style":    "logo geometry + icon language (e.g. thin-line minimalist, solid glyphs, 3D clay)",
  "brand_voice":   "personality in 5 keywords (e.g. Trustworthy, Disruptive, Playful)",
  "ui_elements":   "border-radii, shadow usage, spacing patterns, notable components"
}
```

- **Reference image supplied:** read it (vision) and extract the DNA from what you see.
  Be specific — read *actual* hexes/weights/shapes where the image shows them, not guesses.
- **No reference image:** derive the DNA from the user's own description. If the brief is
  too thin to fill a key responsibly (e.g. no colours mentioned), name the most
  reasonable default for that brand type and say "assumed" so the user can correct.
- Do not over-ask. One short clarifying question is fine if the brief has no visual
  direction at all; otherwise proceed with reasonable assumptions.
- **This DNA is the contract.** Every later stage must be consistent with it. Keep the
  JSON in the conversation so the user can see it was extracted, and pass it to
  `generate_design_concepts`.

## 2. generate_design_concepts

Input: user brief + the DNA JSON (and reference image path, if any). Output: **3 distinct
concepts**, each `{title, description, prompt}`.

```json
{
  "ideas": [
    {
      "title":       "concise, evocative name",
      "description": "layout, palette, typography, key visual elements; how it serves the brief and stays on-DNA",
      "prompt":      "the image-generation prompt for this concept (prompt-guide craft, see below)"
    },
    {
      "title":       "… a genuinely different theme/layout/composition",
      "description": "…",
      "prompt":      "…"
    },
    {
      "title":       "… a third arrangement that isn't a minor variant of the first two",
      "description": "…",
      "prompt":      "…"
    }
  ]
}
```

Distinctness matters: two concepts that are colour/title swaps of each other are one idea.
Aim for different composition/theme/layout between the three. If the user asked for a
*particular* kind of image, the concepts orbit that; if not, cover genuinely different
registers (e.g. photoreal product, editorial flat-lay, bold graphic).

**Craft each `prompt` with `references/prompt-guide.md`** (read it before writing):
sentence-based scene descriptions 50–100 words, critical elements early, lighting/mood/
camera or medium, any text quoted and < 25 chars, composition stated explicitly. When a
reference image is supplied, describe only what it does *not* already fix — the model
sees the reference and follows it.

If the image is to carry words, the prompt must do **both** halves of the text job: quote
each requested string *and* forbid everything else ("the only text is X — no other words,
letters, numbers, watermarks or signatures"). A character budget alone is not a stop
signal; models fill empty space with invented taglines and badges. If a concept needs
more than a phrase or two of legible type — a UI mockup, a landing page, dashboards,
packaging with body copy — the honest answer is that this is a *layout* problem, not a
diffusion problem, and the layout route is the **default for these briefs, not a
fallback**: author it as HTML/CSS and rasterise it (headless Chrome at 2× scale gives
vector-crisp glyphs and exact spacing), then bring the render back through stage 4 like
any other candidate. Staying on diffusion is defensible only when the image *depicts* a
device or a page rather than being one — and even then it must carry a phrase or two of
type at most. Say which route you took in the deliverable.

This is stated firmly because a diffusion run on a focus-app landing page stayed inside
the text budget, passed every assertion, and was still rated "not good enough" by the
user — who rated a headless-Chrome render of the same brief above it. The gate and the
assertion set both went green on the weaker image. A rubric that cannot see the
difference must not be allowed to make the decision.

## 3. generate_idea_image

Run the chosen concept's `prompt` through `hg_image.py`. Locate the skill from wherever it
is installed in the current harness (it is not always under `~/.claude/`); any `python3`
works — the script re-execs into the Hermes venv itself when a hermes-agent checkout is
reachable and configured:

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-<dir containing SKILL.md>}"
PY=python3
"$PY" "$SKILL_DIR/scripts/hg_image.py" generate \
  --prompt "<concept prompt>" \
  [--provider <name>] [--model <id>] [--aspect landscape|square|portrait] [--size WxH] \
  [--ref <reference image path>]... \
  [--api-key KEY] [--base-url URL] [--endpoint PATH] [--mode auto|hermes|standalone] \
  --out "<workspace>/attempt-N.png" --json [--fallback]
```

Provider/model policy (the user was explicit):
- **Any credentialed provider, FAL included** — the earlier FAL exclusion was lifted on
  2026-09-15. What the policy still requires is *explicitness*: the script selects by
  name or by its preference order among credentialed providers only, and the deliverable
  names the provider and model that actually produced the image. Hermes can reach FAL by
  config-driven fallback (an unconfigured or `nous` selection maps onto it), so read the
  payload's provider rather than assuming the one you asked for.
- The script routes Hermes' registry when a hermes-agent checkout is present (modes 1–2)
  and falls back to built-in adapters (alibaba, openai/OpenAI-compatible, openrouter,
  fal, gemini) on env credentials alone (mode 3); the payload's `route` and
  `credential_source` say which path served the image. See SKILL.md → *Provider routing*.
- If the user named a provider or model, pass `--provider X --model Y` verbatim.
- Otherwise **auto-pick**: omit `--provider`; the script picks the first credentialed
  provider in its preference order. Add `--fallback` so that if the first provider
  fails (quota 429, auth, API error) it automatically retries the remaining credentialed
  backends and reports which one actually produced the image (`attempts` in the payload).
- If the user supplies their own API key for a provider, pass `--provider X
  --api-key <key>`; the key is used only for that call and never persisted. An unknown
  provider name plus `--base-url` (optionally `--endpoint`) targets a custom
  OpenAI-compatible server directly.
- For slide/deck artwork, request the canvas with `--size 1920x1080` (or the deck's own
  pixels) and treat `size_note` + measured `width`/`height` as the delivered truth.
- Use `hg_image.py list` first when you need to see which providers are credentialed
  ("OK" rows only; `fal` and non-credentialed providers are marked `--`).

Read the `--json` result. On `"success": true` the image is at `payload.image` — note the
provider and model actually used (the payload reports them). On failure, surface the error
and either retry with a different provider/`--fallback` or report to the user; do not
silently accept a failed generation.

When the pipeline carries a reference image (`--ref`), the model must accept image input —
a text-only model either 400s or silently ignores the reference. The script restricts the
candidate providers to reference-capable ones automatically and errors clearly (naming the
alternatives) if none qualify. Do not "fix" a failure by dropping `--ref`; that discards
the brand fidelity the pipeline exists to preserve — change the provider instead.

## 4. evaluate_image — the accept gate

### First: is the depicted scene actually possible?

The five criteria below are **design** criteria. They measure aesthetics, contrast,
repetition, alignment and proximity — so an image of an impossible scene can score 5s
across the board and sail through. Check semantics *before* you score, and separately:

- **Sides of a divider.** In any scene split by a threshold — a net, a counter, a table,
  a doorway, a starting line — are the participants on the sides the brief implies? This
  is the most common semantic failure of all: a model given "two players and a net" will
  put both players on the same side, or render the net as a backdrop behind the action
  instead of a plane through it.
- **Ground and gravity.** Are the subjects on the surface they are supposed to be on, and
  are held objects in the hand that holds them? A beach scene that drifts onto open
  water, or a figure leaping over surf instead of sand, has not met the brief.
- **Counts and anatomy.** The number of players, products, wheels, limbs and fingers the
  brief implies — no more, no fewer.
- **All the other laws of physics.** Unless the brief *explicitly* asks to break them, the
  scene must obey every law of physics — not only gravity. Light falls from one direction
  and casts one consistent set of shadows; reflections and the horizon agree with the
  viewpoint; liquids pour down and pool; solids rest on what supports them instead of
  intersecting it; scale holds between objects in the same frame; a body's limbs bend the
  way bodies bend. **The default is a lawful world.** A "magical", "surreal" or "dreamlike"
  brief is *not* a licence to break physics — it licenses a style, not broken mechanics.
  Only an explicit instruction ("people floating with no gravity", "a waterfall running
  upward") turns a broken law into the point rather than a defect.
- **Quantity indicators must agree with their own labels.** Any dial, gauge, progress bar,
  meter, percentage, chart or counter that displays a value must be consistent with every
  other statement of that value in the image. This applies to UI mockups and dashboards as
  much as to scenes: a ring, a printed number and a caption are three statements about one
  quantity and they have to agree.

  **And the convention has to be fixed, not assumed.** A progress ring is ambiguous on its
  own — a filled arc can mean *elapsed* (a progress bar's idiom) or *remaining* (a
  countdown's idiom) — so the filled fraction is only checkable against a stated
  convention. Where the image itself settles it (a label reading "remaining", a depleting
  countdown, a matching reference design), use that. Where nothing settles it, the design
  is under-specified: say so, and fix it by **writing the convention into the design**
  rather than by picking one silently. Two readings that each look self-consistent are not
  a pass — an indicator whose meaning the viewer has to guess is a defect in a mockup.

  **When you control the source, derive the graphic from the number.** On the layout route
  this is the whole fix and it costs nothing: compute the arc's `stroke-dasharray` /
  `dashoffset` from the same variable you print as the readout, so the ring *cannot*
  disagree with its label. Never hand-author the two independently. They drift, and a
  silent re-roll will "fix" the text while leaving the arc alone.
- **An interactive or transient state needs a visible cause.** The UI analogue of an
  impossible scene, and the one a mockup is most likely to fail: a popup, toast, tooltip,
  dropdown, modal, hover highlight, drag handle or selection state drawn as though it were
  a permanent fixture, with nothing on screen explaining why it is there. A toast implies a
  notification just fired; a tooltip implies a cursor resting on its trigger; a dropdown
  implies a click on the control that opened it; a drag ghost implies a finger or cursor
  mid-drag. If the cause is not in the frame, the mockup depicts a state that cannot exist —
  a reviewer reads it as a bug, not as a design. So: either **show the cause** (draw the
  cursor over the trigger, highlight the active control, dim the page behind a modal), or
  **show the resting state instead**. Never both a transient element and no cause.

  This is not pedantry about pixels. A landing-page mockup with two notification popups and
  no cursor, no trigger and no notification affordance anywhere on the page was rated by
  the user as broken — and no design criterion could see it, because the popups were
  beautifully typeset. Same failure mode as the volleyball poster, different medium.

A scene that fails any of these is a **gate failure regardless of its design scores**. It
cannot be averaged away by four 5s and a 4.5, and the scorecard must not imply otherwise.
Say plainly in `feedback` that the scene is impossible and name the correction.

This rule exists because the gate missed exactly this case: a tournament poster scored
**4.8/5 and passed 8/8** while showing both opposing players airborne on the *same* side
of the net, leaping over open surf. Each design score was honest — it is a handsome
poster — and every one of them was blind to the fact that the scene cannot happen. The
user caught it, not the pipeline. Never let a rubric of five design criteria be the only
thing standing between a broken scene and the user.

### Then score the design

Look at the generated image (read the PNG with vision) **and** judge it against the DNA
and the user brief. Score five criteria, each 1 (poor) to 5 (excellent), as an integer or
half-integer:

| Criterion        | What it measures                                                     |
|------------------|----------------------------------------------------------------------|
| visual aesthetic | overall appeal + brand alignment (compare colours/logo/typography against the DNA) |
| contrast         | unique elements stand apart in colour, shape, direction               |
| repetition       | consistency of components, patterns, and motifs across the image      |
| alignment        | grid consistency, horizontal/vertical flow, clean structure            |
| proximity        | logical grouping of related elements; clear hierarchy                  |

Emit the scorecard as JSON:

```json
{
  "scores":   {"visual_aesthetic": 4, "contrast": 4, "repetition": 3.5, "alignment": 4, "proximity": 4},
  "overall":  3.9,
  "pass":     false,
  "feedback": "Specific, actionable prompt changes that would fix what failed"
}
```

- `overall` = mean of the five scores, rounded to one decimal.
- `pass` = `overall >= 4.0`.
- **Every criterion is scored honestly.** A pretty image that ignores the brand DNA scores
  low on visual_aesthetic even if it would be great for another brief.

### Retry loop (max 2 retries per concept)

If `pass` is false, do **not** just re-run the same prompt — that wastes the retry. Fold
the `feedback` into a *revised* prompt (read `references/prompt-guide.md` again: the fix
is usually more specific, not longer) and regenerate. Keep the same reference image.
Re-evaluate. Record each attempt's score so the user sees the trajectory.

- Attempt 1 fails → revise prompt → attempt 2.
- Attempt 2 fails → revise again → attempt 3.
- **After 2 failed retries (3 attempts total) for a concept, stop fighting it.** Either
  move to a different concept from step 2, or — if all concepts are exhausted — stop and
  tell the user honestly: deliver the best attempt, the scorecards, and the feedback, and
  ask whether to change the brief/provider rather than silently shipping a bad image.

### Known limits worth honouring

- **Text in images**: if a concept needs crisp text and the retries keep garbling it,
  generate the image without text and overlay the type afterwards (compositing, not
  diffusion). Don't burn all retries on the same lost cause.
- **An impossible scene outranks a beautiful one.** The five criteria are design criteria,
  and a physically incoherent scene can score 5s on every one of them — a poster with both
  opposing players on the same side of the net scored 4.8/5 and passed 8/8 assertions.
  Check semantics separately and first (see stage 4). Two players on one side of a net is
  not a nit, it is the brief unmet, and no amount of visual merit averages it away. The
  same goes for the rest of physics: shadows that disagree with the light, a cup
  intersecting the table it sits on, a hand with six fingers — unless the brief asked for
  the impossible, an unlawful world is a failed brief.
- **Check an indicator against a convention you can name.** A semantic rule that fires on
  an unstated convention can make an image *worse*. A focus-timer mockup was failed on
  "the arc shows 62% elapsed but the readout says 17:42 remaining", the readout was
  changed to 09:30 to agree — and the delivered image then disagreed with its own ring
  under the countdown convention its label ("remaining") and its own reference design both
  used. The first attempt had been the coherent one. Before you fail an indicator,
  **measure the graphic and name the convention you are measuring against**; if the
  convention is not fixed by the image, that is the defect, and it is fixed by deriving the
  graphic from the number, not by rewriting one of the two numbers.
- **Unrequested text is a gate failure, not a quibble.** Read every string in the image
  and compare against what the brief asked for — *wrong* text and *extra* text both fail.
  Extra text is the one that slips through, because the image looks good and the invented
  copy is often well-integrated. A poster that renders the requested wordmark plus two
  taglines nobody asked for has not met the brief, however handsome it is.
- **One change per turn** for editing: if the user wants multiple edits to an image, apply
  them one at a time, re-evaluating each step, rather than re-rolling from scratch.
- If the provider/model the user chose repeatedly fails at the API level even after
  `--fallback`, escalate the raw error to the user — do not paper over it with a lower
  gate.

## Vision: who evaluates

Use the user-specified evaluator if one is provided. Otherwise, if the current
harness/model supports vision or image input, it should evaluate the generated image
directly.

If native vision is unavailable, use the best available vision-capable model or
image-understanding tool exposed by the harness, such as Hermes, OpenClaw, or an
equivalent fallback, if already credentialized.

If no vision-capable evaluator is available, state that a full visual scored gate is not
possible and complete only the parts of the scorecard that can be judged reliably without
image inspection. Do not fabricate visual findings.

## Deliverable

Report concisely, in this order:

1. The extracted DNA (one line per key) — so the user sees the contract.
2. The concepts considered and which one was generated (title + provider + model).
3. The scorecard JSON for the accepted attempt, or the full fail trajectory.
4. The final image path (and `MEDIA:<path>` marker so it can be displayed).

If the image passed, the scorecard should make it obvious *why* it passed. If it could
not pass the gate, say so plainly and show what was tried — never claim success that the
gate didn't confirm.
