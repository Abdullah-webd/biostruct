/**
 * The product's vocabulary, in one place.
 *
 * v1 named every control after the system: epitope, accession, ipTM, harvest.
 * Those are the words the code thinks in, and they made the interface unusable
 * by the people it is for — a microbiologist who knows resistance cold but has
 * never designed a protein, or anyone trying to understand a result in five
 * minutes.
 *
 * So the interface says what a person is trying to do, and the technical term
 * is kept alongside rather than thrown away: it is what they will need when
 * they talk to a structural biologist or read the export. `term()` renders
 * both — plain words first, the real name in tow.
 */

export type Term = {
  /** What the interface calls it. */
  plain: string;
  /** The technical name, kept so the user can carry the result elsewhere. */
  technical?: string;
  /** One sentence a non-specialist can act on. */
  meaning: string;
};

export const TERMS = {
  target: {
    plain: "the protein to attack",
    technical: "target protein",
    meaning:
      "The protein the bacterium uses to survive the drug. Stop it working and the existing antibiotic may work again.",
  },
  accession: {
    plain: "protein record",
    technical: "UniProt accession",
    meaning:
      "The worldwide ID for this exact protein, so there is no doubt which one was used.",
  },
  epitope: {
    plain: "the site to attack",
    technical: "epitope",
    meaning:
      "The specific patch on the protein's surface the designed protein should grab. Usually the part that does the damage.",
  },
  residue: {
    plain: "building block",
    technical: "residue",
    meaning:
      "Proteins are chains of amino acids. Each link is a residue, and each has a number used to refer to it in papers.",
  },
  binder: {
    plain: "designed protein",
    technical: "binder",
    meaning:
      "A small protein the model invents from scratch, shaped to stick to the site you chose.",
  },
  structure: {
    plain: "3D shape",
    technical: "structure",
    meaning:
      "A protein's job is decided by its shape. These shapes are predicted by a model, not measured in a lab.",
  },
  bindingConfidence: {
    plain: "predicted chance it sticks",
    technical: "binding confidence",
    meaning:
      "The model's own estimate, from 0 to 1, that this designed protein actually grabs the target. Most designs score very low, and that is normal.",
  },
  iptm: {
    plain: "how well the two fit together",
    technical: "ipTM",
    meaning:
      "How confident the model is about the way the two proteins sit against each other. Higher is better.",
  },
  pae: {
    plain: "how certain that fit is",
    technical: "interface PAE",
    meaning:
      "The model's error estimate at the point of contact, in ångströms. Lower is better.",
  },
  structureConfidence: {
    plain: "how certain the shape is",
    technical: "structure confidence",
    meaning:
      "How sure the model is that the designed protein folds into the shape shown. Higher is better.",
  },
  relativeScore: {
    plain: "rank within this run",
    technical: "relative score",
    meaning:
      "Position against the others in this batch only. The best of a bad batch still scores near 1, so read it next to the predicted chance, never alone.",
  },
} as const satisfies Record<string, Term>;

export type TermKey = keyof typeof TERMS;

/** One plain sentence per step, shown before anything else on that step. */
export const STEP_INTRO = {
  target:
    "Bacteria beat an antibiotic by making an enzyme that destroys it. Pick that enzyme here — if you chose an infection, we have already looked up the usual culprit for you.",
  epitope:
    "Now choose which part of that protein to attack. The catalytic site — where it chews up the antibiotic — is usually the right answer, and it is pre-selected for you.",
  design:
    "The model now invents small proteins from scratch, shaped to grab the site you chose, and predicts how each one would sit against it.",
  results:
    "Here is what came back, best first. Read the predicted chance it sticks alongside the rank: a batch where nothing worked still produces a ranked list.",
} as const;

/**
 * Step names, as a person would say them.
 *
 * "Their protein" and "Our designs" rather than "Protein" and "Design",
 * because the app deals in two opposite kinds of protein and calling both of
 * them "protein" was the single most confusing thing in it. One is the
 * bacterium's, there is exactly one of it, and you pick it. The others are
 * ours, there are ten of them, they did not exist an hour ago, and they are
 * what gets ranked. The step names now carry that distinction on every screen.
 */
export const STEPS = [
  { key: "target", label: "Their protein" },
  { key: "epitope", label: "Where to hit it" },
  { key: "design", label: "Our designs" },
  { key: "results", label: "Results" },
] as const;

/**
 * The two-kinds-of-protein distinction, in one place, so the contrast is
 * worded identically wherever it is shown.
 */
export const TWO_PROTEINS = {
  theirs: {
    title: "Their protein",
    body: "The bacterium's own enzyme — the thing destroying the antibiotic. There is one of it, it already exists, and you pick it. This is that step.",
  },
  ours: {
    title: "Our designs",
    body: "Brand-new proteins the model invents to jam that enzyme. There are ten or more, none of them existed before, and nobody knows which is any good — which is why they get ranked at the end.",
  },
} as const;

/** Plain words for each binding band, as a phrase that completes a sentence. */
export const BAND_WORDS: Record<string, string> = {
  high: "a good chance it sticks",
  moderate: "some chance it sticks",
  low: "a low chance it sticks",
  negligible: "almost no chance it sticks",
  "not reported": "no estimate reported",
};

/**
 * The API reports missing measurements by their raw key, because that is the
 * stable contract. Nobody should read "ipsae_min" in a sentence, so the
 * interface translates them here.
 */
export const METRIC_WORDS: Record<string, string> = {
  binding_confidence: "the chance it sticks to the target",
  iptm: "how well the two fit together",
  ipsae_min: "a second measure of how well the two fit together",
  min_interaction_pae: "how certain that fit is",
  structure_confidence: "how certain its shape is",
};
