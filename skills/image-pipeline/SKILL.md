---
name: image-pipeline
description: Use whenever the user asks to generate an image, visual, mockup, thumbnail, cover, poster, ad, UI art or brand/design asset from a description, brand, or reference image — even without naming a pipeline or provider. Quality-gated design pipeline (brand_analysis → generate_design_concepts → generate_idea_image → evaluate_image; accept only at ≥ 4.0/5 after up to 2 retries). Works in any harness: prefers Hermes' configured providers, with built-in adapters for Alibaba, OpenAI/OpenAI-compatible, OpenRouter, FAL, Google Gemini; handles user-named providers, user API keys, custom endpoints, and slide artwork for PowerPoint skills.
compatibility: Python 3.11+ with stdlib only (no packages to install). Works in any harness. When a hermes-agent checkout is present (HERMES_AGENT_REPO or ~/.hermes/hermes-agent) the driver additionally uses Hermes' provider registry and switches to its venv python by itself.
---

# Image Pipeline

Generate images that pass a quality gate before they are delivered. The image is not
accepted on first generation — it must survive an evaluation stage (score ≥ 4.0/5, up to
2 retries) first. This is what separates "an image" from "an image worth shipping."

## Why a pipeline at all

An LLM can describe a picture in seconds; diffusion models will happily render whatever
they're pointed at. The result without a gate is a string of mediocre, off-brief images
the user has to reject one by one. The pipeline — analyze the brief into Visual Design
DNA, generate *concepts* before any pixels, then evaluate and retry against the DNA —
front-loads the design thinking the model otherwise never does, and it gives the user a
scorecard instead of a shrug.

You still get all the flexibility: any credentialed provider — FAL included — any model
on that provider, and the user's own API key (or endpoint) when they bring one. Selection
is always explicit and credentialed, and the deliverable always names the provider that
actually produced the image.

## The four stages (full reference: `references/pipeline.md`)

Read `references/pipeline.md` when you start the task and follow its stage order. The
steps in brief:

1. **brand_analysis** — read the reference image (if supplied) or the user's description,
   and extract the Visual Design DNA as JSON: `colors`, `typography`, `icon_style`,
   `brand_voice`, `ui_elements`. This DNA is the contract everything else checks against.
2. **generate_design_concepts** — produce 3 genuinely distinct concepts, each with a
   `title`, a `description`, and a `prompt` crafted per `references/prompt-guide.md`.
   For briefs that need more than a phrase or two of legible type — **UI mockups, landing
   pages, dashboards, packaging with body copy** — take the **layout route** (author the
   page as HTML/CSS, rasterise with headless Chrome at 2×) rather than diffusion. That is
   the default for those briefs, not a fallback: they are pages to be read, and a layout
   engine gives exact spacing and vector-crisp glyphs that diffusion only approximates.
   Say which route you took in the deliverable.
3. **generate_idea_image** — run the selected concept's prompt through
   `scripts/imagegen.py` (details below). The script handles provider discovery, the
   Hermes-or-standalone routing ladder, and (with `--fallback`) automatic retry through
   the other credentialed providers.
4. **evaluate_image** — visually score the generated image against the DNA across five
   criteria (visual aesthetic, contrast, repetition, alignment, proximity). Pass is
   `overall ≥ 4.0`. If it fails, fold the feedback into a *revised* prompt and regenerate
   — max 2 retries per concept. Only deliver once the gate clears, and always show the
   scorecard.

Read `references/prompt-guide.md` before writing any image prompt (concepts and retries
both need it). It covers sentence-style briefs, structure, length, text-in-images limits,
composition, and per-model notes.

## Generating the image (stage 3)

The pipeline drives `scripts/imagegen.py`. Any `python3` works — when Hermes is present
the script re-execs into its venv by itself.

Resolve `SKILL_DIR` from where this skill actually lives in the current harness — it is
not always under `~/.claude/` (e.g. `$(dirname <this SKILL.md>)`, or `$CLAUDE_SKILL_DIR`
if the harness sets it; scripts themselves never depend on the harness, they locate
their own packages via `__file__`).

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-<dir containing this SKILL.md>}"
PY=python3   # the script re-execs into $HOME/.hermes/hermes-agent/venv/bin/python itself
"$PY" "$SKILL_DIR/scripts/imagegen.py" list       # see which providers are credentialed
"$PY" "$SKILL_DIR/scripts/imagegen.py" generate \
  --prompt "<concept prompt>" \
  [--provider openrouter|openai|openai-codex|gemini|fal|alibaba|<custom>] \
  [--model <model id>] \
  [--aspect landscape|square|portrait] [--size 1920x1080] \
  [--ref /path/to/reference.png]... \
  [--api-key KEY] [--base-url URL] [--endpoint /chat/completions] \
  [--mode auto|hermes|standalone] [--timeout SECONDS] \
  --out "<workspace>/attempt-1.png" --json [--fallback]
```

### Provider routing (what the script does before any pixels)

1. **Hermes present and configured** → Hermes' own image_gen providers are used, secrets
   resolving through Hermes' credential ladder (`route: "hermes-registry"`,
   `credential_source: "hermes-ladder"`).
2. **Hermes reachable but not configured** → the same provider plugins are driven by the
   env vars they declare (`credential_source: "env"`), and env-credentialed built-in
   adapters join the `--fallback` chain.
3. **No Hermes at all** → built-in standalone adapters (alibaba, openai / any
   OpenAI-compatible endpoint, openrouter, fal, gemini) authenticate from environment
   keys: `ALIBABA_TOKEN_PLAN_API_KEY` / `DASHSCOPE_API_KEY`, `OPENAI_API_KEY`,
   `OPENROUTER_API_KEY`, `FAL_KEY`, `GEMINI_API_KEY` (full matrix:
   `docs/image-pipeline.md`).
4. **Bypass (any case)** → `--provider X --api-key K [--base-url U] [--endpoint E]`;
   an unknown provider name with `--base-url` is treated as a generic
   OpenAI-compatible endpoint. `--mode` forces 1 vs 3.

The `--json` payload's `route`/`credential_source`/`provider`/`model` record exactly who
served the image — read them into the deliverable, every time.

### Collaborating with presentation/PowerPoint skills

When a deck skill asks for slide artwork, it is the *caller* and this pipeline stays in
charge of quality:

- Pass the deck's brand DNA (extracted from the template) in place of re-deriving stage 1 —
  but never skip stages 2 and 4; the 4.0 gate is not negotiable to fill a placeholder.
- Ask for the slide canvas with `--size 1920x1080` (16:9) — or the deck's own pixel
  canvas. Providers rarely serve arbitrary pixels: the payload's `notes` + measured
  `width`/`height` say what actually arrived. Place artwork with crop-to-fill or upscale —
  never stretch.
- Write artwork to `<deck-project>/art/slide-<NN>-attempt-<N>.png` and record
  provider/model into the deck's notes/sources.
- Text belongs in the deck's text boxes: burn at most 25 characters into the art, and
  only when the design demands it.

Rules, in the user's own words:

- **Selection is explicit and credentialed.** Every registered provider — FAL included —
  is selectable once it has credentials; `imagegen.py list` shows which do. Nothing routes
  anywhere by surprise: the script picks by name or by its preference order, and the
  `--json` payload reports the provider and model that actually produced the image, so a
  config-driven fallback (Hermes maps an unconfigured or `nous` selection onto FAL) is
  always visible in the deliverable rather than hidden.
- **Prefer Hermes' configuration when present; otherwise the env.** With a hermes-agent
  checkout, its configured image providers come first and secrets resolve through Hermes —
  the script stores nothing. Without Hermes (or with it unconfigured), the script picks
  among env-credentialed built-in adapters, under the same explicitness rule.
  `imagegen.py list` shows which are actually credentialed (OK rows); only those are
  selectable.
- **Honour an explicitly named provider/model.** If the user says "use Alibaba Token
  plan" or "use Nano Banana 2", pass `--provider alibaba` / `--model
  google/gemini-3.1-flash-image` exactly as named. Check the models the provider offers
  with `imagegen.py list --models` if unsure.
- **Accept the user's own API key.** `--provider X --api-key <key>` injects the key for
  that single call; nothing is persisted. This is how a user with their own key on an
  otherwise-uncredentialed provider gets in.
- **Auto-pick with resilience.** When the user names no provider, omit `--provider` and
  pass `--fallback`: the script tries the first credentialed provider and, if it fails
  (e.g. quota 429, auth, API error), automatically retries the remaining credentialed
  backends and reports which one actually produced the image (the payload's `attempts`
  lists what failed before the winner).
- **Exact-pixel briefs go through `--size`, and the snap trail is part of the truth.**
  For deck canvases (`--size 1920x1080`) read `notes` and the measured
  `width`/`height` from the payload — if the provider snapped, say so; never claim
  unsnapped pixels.
- **Reference images need a model that accepts image input.** Passing `--ref` to a
  text-only model 400s (or, worse, silently ignores the reference and bills you for a
  text-to-image picture). The script therefore narrows to reference-capable providers
  whenever `--ref` is present, and fails loudly — listing the reference-capable
  alternatives — rather than quietly dropping the reference. Never remove `--ref` to make
  a failing call succeed; switch provider instead.
- **Name the exact text, and cap it.** When the image should carry words, the prompt must
  quote each string *and* state that no other text may appear ("the only text is X — no
  other words, letters, numbers, watermarks or signatures"). Left unstated, models invent
  taglines and badges: one eval poster grew ~40 characters of copy nobody requested and
  every assertion still passed. Keep the total under 25 characters, and never re-roll to
  fix text — composite it (see prompt-guide).
- **State the sides of any divider.** If the scene is split by a threshold — a net, a
  counter, a table, a doorway, a starting line — say which subject is on which side and
  name the divider's orientation. Left unstated, models put every subject on the same side
  or render the divider as a backdrop behind the action: one eval poster showed both
  opposing players airborne on the *same* side of the net and still passed the gate at
  4.8/5. Say what the subjects stand on or leap over, too.

Read the `--json` result. Note the `provider` and `model` the payload reports — they feed
the scorecard and the retry decisions.

## Evaluation (stage 4) — the accept gate

- Evaluate by actually *looking* at the generated PNG — with vision on the current model,
  or the best vision-capable evaluator the harness offers (see
  [pipeline.md](references/pipeline.md) → *Vision: who evaluates*) — side by side with the
  DNA and brief. If nothing in this runtime can see the image, say so; do not invent visual
  findings.
- **Check the scene is possible, before you score anything.** The five criteria are
  *design* criteria, so an impossible scene can score 5s across the board: a poster showing
  both opposing players airborne on the *same* side of the net scored 4.8/5 and passed 8/8
  assertions. Check the semantics separately and first — sides of any divider (net,
  counter, table, doorway), what the subjects are standing on or leaping over, and counts
  and anatomy. **Then the rest of physics**: unless the brief explicitly asks to break a
  law, the world must be lawful — light from one direction casting one consistent set of
  shadows, things resting on what supports them rather than intersecting it, reflections
  agreeing with the viewpoint, liquids falling, bodies bending. A "surreal" or "dreamlike"
  brief licenses a style, not broken mechanics. **Then any quantity indicator** — a dial,
  gauge, progress bar, meter or chart must agree with every other statement of its value in
  the image, and the reading convention must be *fixed*, not assumed: a filled arc can mean
  elapsed or remaining, so measure the graphic and name the convention you are checking
  against. If nothing in the image fixes it, that ambiguity *is* the defect. When you
  control the source, derive the graphic from the number (arc computed from the value, not
  hand-authored beside it) so they cannot drift. A scene that fails any of these is a gate
  failure regardless of its design scores, and you must say so in `feedback` rather than
  letting four 5s average it away.
- Score the five criteria 1–5, compute `overall` (mean), and set `pass = overall ≥ 4.0`.
- **Read the text back.** Before scoring, read every string rendered in the image and
  compare it against the strings the brief actually asked for. Two distinct failures, and
  both are gate failures: text that is *wrong* (garbled, misspelled, melted) and text that
  is *extra* (invented taglines, badges, a second brand's wordmark). The second hides
  behind a good-looking image, so check for it on purpose. An image carrying unrequested
  copy cannot pass on visual merit alone.
- On failure your `feedback` must be specific and actionable — "make the palette warmer" is
  weak; "swap the background from grey to the brand's sand tone (#D9C6A5) and drop the
  serif headline for the 600-weight sans" is a plan the model can execute.
- Up to **2 retries per concept** (3 attempts total). Each retry regenerates with a
  *changed prompt*, never a silent re-roll. If the concept still won't clear the gate,
  move to another concept from stage 2. If all concepts fail, tell the user plainly and
  hand over the best attempt + scorecards + feedback.
- **Text that keeps garbling**: stop fighting the model — overlay crisp text afterwards.
  Don't burn the retries on it (see prompt-guide).

## Deliverable format

Report concisely, in this order: the extracted DNA (one line per key), the concept
generated (title + provider + model), the accepted scorecard JSON, and the final image
path with a `MEDIA:<path>` marker. When the image failed the gate, show the fail
trajectory (attempt → score → feedback → revised) and say clearly that it was not
accepted; never report an ungated image as a pass.

## Troubleshooting

- **"No credentialed image provider found"** (exit 2) — no key resolved through Hermes
  or the environment. Export one of the provider keys (matrix:
  `docs/image-pipeline.md`), configure `hermes tools` → Image Generation when Hermes is
  your agent, or pass `--provider X --api-key <key>` (add `--base-url` for a custom
  OpenAI-compatible endpoint). `imagegen.py list` shows what's reachable and why.
  Note: OAuth-only backends like `openai-codex` exist in Hermes mode only.
- **Provider available but call fails** (401/429/API error) — run again with
  `--fallback` so the next credentialed backend takes over; if the user named a provider
  explicitly, surface the error verbatim and ask rather than silently switching.
- **Reference image rejected** (unsupported type/size) — convert to a PNG/JPEG under
  25 MB first; pass the new path with `--ref`.
- **Vision unavailable** — the gate needs to *see* the image. Fall back to any
  vision-capable evaluator the harness exposes (a credentialed Hermes/OpenClaw vision
  model, or an equivalent); if there is none, tell the user a full visual gate isn't
  possible, score only what can be judged without looking, and do not fabricate the rest.
