# Biostruct — design direction (v2)

v1 was dark with an acid-green accent. Anthropic's `frontend-design` guidance
lists that exact combination as one of the clusters that reads as generated
design, along with three other things v1 did on every screen: tracked-out
ALL-CAPS eyebrows above headings, meta strings joined with middle dots, and a
monospace face for small labels. It also did the deeper thing wrong — it named
every control after the system rather than after what a person is trying to do.

So v2 changes both the look and the language.

## Who this is for

Not only the handful of people who can already design proteins. A clinical
microbiologist who knows resistance intimately but has never heard of an
epitope should be able to get to a result, and a judge with no biology should
understand what happened.

That rules out the system's vocabulary as the interface. "Epitope",
"UniProt accession", "author numbering", "ipTM" describe how the thing is
built. People care about: which bug, which protein makes it resistant, where to
attack it, what came out, is any of it any good.

## Light, not dark

White paper. This is a document you'd put findings in, not a console. It reads
as clinical and calm, it prints, it survives a projector in a bright room, and
it is the opposite of the dark-plus-neon default.

The one place that stays dark is **the 3D viewport**, because a specimen is lit
against a dark field in every piece of structural software there is. A dark
panel inset into a white page is the single bold move, and everything else
stays quiet around it.

| Role | Hex | Use |
|---|---|---|
| paper | `#FFFFFF` | page |
| wash | `#F7F7F5` | panels, table stripes |
| rule | `#E4E4E0` | hairlines |
| ink | `#16181D` | body text |
| ink-soft | `#5B6270` | secondary text |
| ink-faint | `#8A909C` | captions, units |
| accent | `#1F5FAE` | links, primary action, focus |
| concern | `#B4451F` | resistance, warnings, "nothing worked" |
| growth | `#1F7A54` | the designed protein, good outcomes |
| specimen | `#0E1117` | the 3D viewport only |

Blue and rust rather than neon: it is the palette of a medical chart, and the
rust carries the emotional weight of the subject without shouting.

## Type

**IBM Plex** throughout, in three cuts. One family, so the page is disciplined;
Plex was drawn for technical products and has a warmth that Inter lacks.

- **Plex Serif** for headlines. A serif on white reads as considered and
  medical rather than start-up.
- **Plex Sans** for everything a person reads or clicks.
- **Plex Mono** only where characters must line up and be counted: protein
  sequences, residue numbers, metric columns. Never for a label — that was a v1
  tell.

Sentence case everywhere. No ALL-CAPS labels.

## Language

A glossary lives in one file (`lib/words.ts`) so the whole product says the same
thing, and so the wording can be reviewed as writing rather than hunted through
components.

| System says | Interface says |
|---|---|
| Epitope | the site to attack |
| UniProt / accession | protein record |
| Author residue numbering | residue numbers as written in papers |
| Design run | designing proteins |
| Binding confidence | predicted chance it sticks |
| ipTM | how well the two fit together |
| min interaction PAE | how certain that fit is |
| Structure confidence | how certain the protein's shape is |
| Candidate | designed protein |
| Harvest / reconciler | *(never shown)* |

Every step opens with one plain sentence saying what is about to happen and
why, written for someone who has never done this.

## Layout

v1 put a seven-item rail on the left. Four of those were things the user had to
do and three were decoration. v2 shows the four real steps as a single
horizontal progress line at the top, because it is a sequence and that is the
honest use of numbering.

```
┌──────────────────────────────────────────────────────────────┐
│  Biostruct                                   project · menu  │
├──────────────────────────────────────────────────────────────┤
│  ①━━━━━━━━②━━━━━━━━③━━━━━━━━④                                │
│  Protein   Site     Design   Results                         │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  One sentence saying what this step is for.                  │
│                                                              │
│  ┌───────────────────────────┐  ┌─────────────────────────┐  │
│  │  the work                 │  │ ███ dark specimen ███   │  │
│  │                           │  │ ███   viewport    ███   │  │
│  └───────────────────────────┘  └─────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## The one-click path

A "Show me an example" button fills in a real, working case — carbapenem-
resistant *Klebsiella*, KPC-2, the catalytic site — and walks it through. Nobody
evaluating this in five minutes should have to know what to type.

## Motion

One orchestrated moment on the landing page, and then restraint. The current
guidance is explicit that fade-up on every section and hover lift on every card
read as generated, so v2 drops those. What stays:

- the specimen rotating, which is the subject behaving the way the real
  software behaves
- the step line advancing when a step completes
- progress that reflects a real job, and results arriving when they arrive
- feedback on controls, because a control that does not answer feels broken

## Still true from v1

The honesty is the product. A test-mode run says so plainly. A protein with a
negligible predicted chance says that next to its rank, not in a footnote. A run
where nothing worked says so before showing the list. That survives the
redesign unchanged, in plainer words.
