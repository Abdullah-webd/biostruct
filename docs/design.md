# Biostruct — design direction

Written before any UI code, per the house standard. The brief: production-ready,
and the 3D view should feel like the molecular software researchers actually
use — sharp, with the specimen rotating under the lens.

## Subject

A computational instrument for designing therapeutic proteins against
drug-resistant bacteria. Its users are microbiologists and computational
biologists, often at African research institutions, who know the clinical
problem intimately and should not have to learn protein-design tooling to act
on it.

The job of the interface: carry someone from "carbapenem-resistant *Klebsiella*
on my ward" to a ranked, honestly-caveated list of candidate binders — without
ever overstating what the computer actually knows.

Its visual world is **not** the AI-startup world. It is crystallography and lab
instrumentation: mmCIF files, Ambler residue numbering with its gaps, ribbon
renderings on a dark field, confidence colouring, fixed-width readouts, the
oscilloscope-green of a measurement that has settled.

## Motion dial

- **7** on the landing page — it has one job, which is to make a stranger
  understand what this does in eight seconds.
- **5** in the app — route transitions, data arriving in sequence, animated
  metric bars, tactile controls. No scroll theatrics in a tool someone uses for
  an hour.
- The **viewer** is the exception and the signature: the molecule rotates
  continuously, because that is what the real software does and it is how a
  structural biologist reads a fold.

## Motion thesis

> Everything behaves like a precision instrument: readouts settle into place,
> the specimen rotates continuously under the lens, and nothing moves unless it
> is reporting a measurement.

That rules out decorative motion by definition, and it justifies the one piece
of continuous animation in the product.

## Palette

Dark ground, because every molecular viewer in the field is dark and a light
one would read as a toy. One sharp accent rather than a spread.

| Role | Hex | Use |
|---|---|---|
| ground | `#0A0E14` | page background, viewer field |
| panel | `#121821` | cards, rails, readout surfaces |
| line | `#222C3A` | hairlines, borders |
| ink | `#E6EDF3` | primary text |
| muted | `#7D8B9A` | secondary text, units |
| **signal** | `#00E5A0` | accent, focus, the designed binder |
| target | `#5B8DEF` | the target protein chain |
| epitope | `#FF4D8D` | highlighted epitope residues |
| caution | `#FFB454` | caveats, low confidence, test-mode |

Chain colours are picked to differ in lightness as well as hue, so binder and
target remain distinguishable with deuteranopia.

## Type

Not Inter, not Geist, not Space Grotesk.

- **Archivo** for UI and display (Archivo Expanded at display sizes) — a
  technical grotesque with real width variation, so headlines can be set wide
  and tight without a second family.
- **JetBrains Mono** for every number, residue identifier, sequence and metric.
  Tabular figures are not decorative here: a column of confidences has to align
  to be readable, and sequences must be monospaced to be countable.

## Layout

The app is not a marketing page and should not be shaped like one.

```
┌────────────┬──────────────────────────────────────────────┐
│  BIOSTRUCT │  project · Carbapenem-resistant K. pneumoniae│
│            ├──────────────────────────────────────────────┤
│ 1 INFECTION│                                              │
│ 2 TARGET  ●│   main canvas                                │
│ 3 EPITOPE  │   (dense, real data, no hero space)          │
│ 4 DESIGN   │                                              │
│ 5 RESULTS  │                                              │
│ 6 COMPARE  │                                              │
│ 7 EXPORT   │                                              │
│            │                                              │
│ ─────────  │                                              │
│ budget     │                                              │
│ $24.50 left│                                              │
└────────────┴──────────────────────────────────────────────┘
```

A persistent left rail of the seven workflow steps, like the function selector
on a bench instrument: it shows where you are in a process, not where you can
navigate. Budget sits at its foot because spending is a first-class concern.

Candidate detail splits the other way: the viewer takes the field, the readouts
sit beside it in a fixed-width column.

```
┌─────────────────────────────────────┬────────────────────┐
│                                     │ rank 1 of 10       │
│         [ rotating complex ]        │ ─────────────────  │
│                                     │ binding   2.35e-06 │
│    binder NANO1  ·  target A        │ ipTM        0.7632 │
│         epitope ·                   │ PAE          3.540 │
│                                     │ fold        0.5773 │
│  ◼ cartoon ◻ surface ◻ spin         │ ─────────────────  │
└─────────────────────────────────────┴────────────────────┘
```

## Signature

**The molecule is the hero, and honesty is the other half of it.**

On the landing page the real KPC-2 structure rotates behind the headline, and
on scroll the camera scrubs from the whole protein down to the catalytic site
with Ser70, Lys73, Ser130 and Glu166 labelling themselves as they arrive. The
thing the product does, shown with the actual thing.

The second half is unusual and deliberate. Most products bury their caveats in
grey 11px text. Here the **verdict is a designed component**: a candidate with a
negligible predicted binding probability says so in the same visual weight as
its rank, and a run where nothing is predicted to bind says that across the top
of the results rather than presenting a cheerful ordered list. The live run that
produced `binding_confidence = 2.35e-06` is exactly why — see
[boltz-api-observed.md](boltz-api-observed.md). Designing the honesty in is the
part a competitor would not copy.

## Set-pieces

Landing (dial 7):
1. **Intro** — molecule fades in already rotating; headline arrives line by
   line; nav and CTA live immediately. ≤ 1.4s.
2. **Scrubbed zoom** — whole protein to active site, pinned, residue labels
   appearing on arrival.
3. **The numbering problem** — a scrubbed diagram of UniProt 69 / author 70 /
   index 39 resolving onto the same residue. Our actual differentiator, and it
   earns its space because it is a genuinely surprising fact.

App (dial 5):
- route transitions with the View Transitions API
- table rows and candidate cards arrive in sequence on data load, not all at once
- metric bars grow from zero on first view only
- skeleton to content, never a spinner on a full page
- SSE run progress that tracks the actual job rather than animating hopefully
- 120ms press feedback on every control, accent focus rings

## What this deliberately is not

No purple-to-blue gradient hero. No centered hero over three identical feature
cards. No emoji as icons. No glassmorphism. No count-up vanity statistics. No
stock laboratory photography — the only imagery is real structural data, because
we have real structural data.
