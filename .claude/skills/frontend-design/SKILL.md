---
name: frontend-design
description: Follow this project's actual web/ conventions before writing any front-end code — plain HTML/CSS/JS, no framework, no build step. Use this skill whenever the task touches a screen, chart, component, or layout in web/, or any "make this look better / this looks off" request.
when_to_use: Triggers include "add a screen", "add a chart", "restyle this", "the layout looks off", "make this look better", or any work on files under web/.
paths: web/**
---

# Front-end work in `web/`

`web/` is three files, no build step, no framework, no `node_modules` — see
`web/README.md` for the full map. The Python server (`promptmeter/server.py`)
serves this folder directly; editing a file and refreshing the browser is the
whole development loop. There is no design tool in front of this code — write
directly against the conventions below, which are the actual rules the
existing 1,683 lines of `app.js` follow, not a style guide someone wrote
separately from the code.

## Before adding anything

1. Read `web/README.md`. It maps `app.js`'s six sections (state/api, helpers,
   components, views, actions, router) and the five existing screens
   (`#dashboard`, `#plan`, `#projects`, `#project/:id`, `#history`, `#setup`).
2. Check whether an existing helper or component already does what you need —
   `esc()`, `usd()`, `tokens()`, `dur()`, `when()`, `ago()`, `ring()`,
   `meter()`, `progress()`, `spark()`, `riskChip()`. Reuse before writing a new
   one.
3. If the change is a new chart or any data visualization (a new meter, ring,
   sparkline, or graph), also load the `dataviz` skill for the underlying
   colour/form guidance — then implement it as hand-written SVG, matching how
   the existing charts in `app.js` are built. This project does not use a
   charting library and should not gain one for a single new chart.

## Rules that are not optional

These are documented in `web/README.md` because each one was a real bug before
it was written down as a rule:

- **Every HTML interpolation goes through `esc()`.** Views build screens by
  string concatenation and assign the result to `innerHTML` — `esc()` is the
  only thing standing between a pasted prompt containing `<script>` and an XSS
  bug. No exceptions, including values that "can't" contain user input.
- **Colours come from CSS custom properties, never literals.** `var(--series-1)`,
  never a hex code, in CSS or in a generated SVG string. This is what makes
  dark mode a single block of `:root` overrides in `styles.css` rather than a
  rewrite.
- **A meter, ring, or progress element with no reading yet renders as the
  hatched `.unknown` state — never as an empty-looking bar.** Empty (0%,
  actually measured) and unknown (nothing measured yet) must stay visually
  distinct; conflating them was a real, fixed bug.
- **Plain language leads, numbers follow.** Where a screen states a
  conclusion (e.g. the Plan screen's risk verdict), the plain-language
  sentence comes first and the exact percentages/dollars sit behind a
  disclosure ("The exact numbers"), not the other way round.
- **The palette is colour-blind-validated** (`web/README.md`'s token table:
  surfaces, text, lines, `--series-1..4`, status, meter track colours). Don't
  introduce a new colour outside that token set without deliberately deciding
  it still passes CVD contrast — check with a teammate or the `dataviz` skill
  rather than eyeballing it.

## Adding a screen

Exactly the procedure in `web/README.md`, nothing more:

1. Write `async function viewThing() { return \`<html>\` }`.
2. Add it to the `VIEWS` map.
3. Add a `<button class="nav-item" data-route="thing">` to `index.html`.

No build, no registration step, no config file.

## When to skip this

Bug fixes, copy edits, and single-property tweaks (one padding value, one
colour swap that's already a token) don't need re-reading the whole
convention set — just make the change and keep it consistent with what's
around it.

## Verifying the result

There is no visual regression tooling here. Start the server
(`python -m promptmeter`, or use the `run` skill to launch and screenshot it)
and actually look at the affected screen in a browser before calling a UI
change done — don't assert it looks right unviewed.
