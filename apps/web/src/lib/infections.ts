/**
 * The infections this platform can be pointed at, written for someone who is
 * not a doctor.
 *
 * v2 asked people to *type* an infection and an organism into empty boxes.
 * That is a wall: you cannot type "carbapenem-resistant Klebsiella pneumoniae"
 * unless you already knew it, and the people who most need to understand this
 * — a judge, a student, a founder — do not. So the app knows them instead, and
 * the user picks.
 *
 * Every entry here is a real, documented resistance problem, and each one maps
 * to the protein that causes it, so choosing an infection also picks the search
 * that finds that protein in step 1.
 *
 * The scope is honest: all four are bacteria that defeat antibiotics by making
 * an enzyme that cuts the drug apart. That is the family of problems this
 * pipeline is built for, and the catalogue does not pretend otherwise.
 */

export type InfectionCase = {
  /** Stable id, used to carry the choice from here into step 1. */
  id: string;
  /** What the project gets called. Plain enough to recognise in a list. */
  projectName: string;
  /** Headline: the problem, in words anyone can follow. */
  headline: string;
  /** The organism, rendered in italic by the UI. */
  organism: string;
  /** Where people actually catch it. */
  where: string;
  /** Why it is hard to treat — the mechanism, in one sentence. */
  how: string;
  /** The name of the enzyme doing the damage. */
  enzyme: string;
  /** Sent to the API as the infection name. The clinical phrase. */
  clinicalName: string;
  /** The UniProt search that finds the protein to attack. */
  proteinQuery: string;
  /**
   * The words a person actually uses when they do not know the clinical name:
   * what they see, where on the body, what they would tell a nurse. Matched
   * literally — no model, no inference, so the behaviour is predictable and
   * can be read off this file.
   */
  symptoms: string[];
  /** How the match is explained back to the user, in their words. */
  symptomSummary: string;
  /** True only for the case this product has been run end to end. */
  worked?: boolean;
};

export const INFECTIONS: InfectionCase[] = [
  {
    id: "kpc-klebsiella",
    projectName: "Klebsiella that survives our last-resort antibiotics",
    headline: "A hospital infection that our strongest antibiotics no longer cure",
    organism: "Klebsiella pneumoniae",
    where:
      "Intensive care units, surgical wounds, catheters and ventilators. It hits people who are already seriously ill.",
    how: "It makes an enzyme that chops carbapenems apart — and carbapenems are the drugs doctors keep back for when everything else has already failed.",
    enzyme: "KPC carbapenemase",
    clinicalName: "Carbapenem-resistant Klebsiella pneumoniae",
    proteinQuery: 'protein_name:"carbapenem-hydrolyzing beta-lactamase KPC"',
    symptoms: [
      "cough",
      "coughing",
      "chest",
      "lung",
      "lungs",
      "pneumonia",
      "breathing",
      "short of breath",
      "phlegm",
      "sputum",
      "fever",
      "hospital",
      "icu",
      "intensive care",
      "ventilator",
      "catheter",
      "sepsis",
      "bloodstream",
      "klebsiella",
      "carbapenem",
      "kpc",
    ],
    symptomSummary: "coughing, chest and lung infections, and infections caught in hospital",
    worked: true,
  },
  {
    id: "ctxm-ecoli",
    projectName: "E. coli that beats the standard antibiotics",
    headline: "The everyday infection that stopped responding to the usual pills",
    organism: "Escherichia coli",
    where:
      "Urinary tract infections and bloodstream infections, caught in the community rather than in hospital. This is the common one.",
    how: "It makes an enzyme that destroys cephalosporins, the antibiotics a clinic would normally reach for first, so treatment has to escalate to stronger drugs.",
    enzyme: "CTX-M beta-lactamase",
    clinicalName: "Extended-spectrum beta-lactamase producing Escherichia coli",
    proteinQuery: 'protein_name:"beta-lactamase CTX-M"',
    symptoms: [
      "urine",
      "urinary",
      "uti",
      "bladder",
      "peeing",
      "weeing",
      "urinating",
      "burning",
      "kidney",
      "e. coli",
      "e coli",
      "coli",
      "stomach",
      "gut",
      "diarrhoea",
      "diarrhea",
      "food",
      "ctx",
      "cephalosporin",
    ],
    symptomSummary:
      "urine and bladder infections, and infections picked up outside hospital",
  },
  {
    id: "oxa-acinetobacter",
    projectName: "Acinetobacter in wounds that nothing clears",
    headline: "A wound infection that resists almost every antibiotic we have",
    organism: "Acinetobacter baumannii",
    where:
      "Burns, trauma wounds, and ventilated patients. It survives on dry hospital surfaces for weeks, so it spreads through wards.",
    how: "It makes an enzyme that breaks down carbapenems, and it usually carries several other resistance genes at the same time, which is why so little works against it.",
    enzyme: "OXA-type carbapenemase",
    clinicalName: "Carbapenem-resistant Acinetobacter baumannii",
    proteinQuery: 'protein_name:"beta-lactamase OXA-48"',
    symptoms: [
      "wound",
      "wounds",
      "burn",
      "burns",
      "cut",
      "injury",
      "trauma",
      "accident",
      "surgery",
      "surgical",
      "skin",
      "ulcer",
      "not healing",
      "pus",
      "infected wound",
      "acinetobacter",
      "oxa",
    ],
    symptomSummary: "wounds, burns and surgical sites that will not heal",
  },
  {
    id: "ndm-enterobacterales",
    projectName: "The NDM enzyme that is spreading between species",
    headline: "A resistance gene that jumps from one bacterium to another",
    organism: "Enterobacterales",
    where:
      "Gut bacteria worldwide. The gene sits on a loop of DNA that bacteria hand to each other, so resistance spreads without the bug itself spreading.",
    how: "The enzyme it codes for uses a zinc atom to cut open nearly every beta-lactam antibiotic there is, and the drugs we use to block other such enzymes do not block this one.",
    enzyme: "NDM metallo-beta-lactamase",
    clinicalName: "NDM-producing Enterobacterales",
    proteinQuery: 'protein_name:"metallo-beta-lactamase NDM"',
    symptoms: [
      "spreading",
      "spread",
      "outbreak",
      "ward",
      "several patients",
      "many patients",
      "everything",
      "nothing works",
      "every antibiotic",
      "multi",
      "multidrug",
      "resistant to everything",
      "ndm",
      "plasmid",
      "gene",
    ],
    symptomSummary:
      "an outbreak moving between patients, or a bug that resists everything tried",
  },
];

/**
 * Match what somebody typed against the catalogue.
 *
 * Deliberately a literal keyword match over the lists above, not a model: it
 * is predictable, it costs nothing, it works offline, and anybody can read
 * this file and know exactly why a suggestion appeared. Results are ordered by
 * how many distinct words matched.
 *
 * This is emphatically not a diagnosis. It maps everyday words onto the four
 * cases this pipeline can actually handle, and the UI says so.
 */
export function matchInfections(
  text: string,
): { infection: InfectionCase; matched: string[] }[] {
  // Pad with spaces so a keyword can be matched at a word boundary without a
  // regular expression per term.
  const haystack = ` ${text.toLowerCase().replace(/[^a-z0-9.\s]/g, " ").replace(/\s+/g, " ")} `;
  if (haystack.trim().length < 3) return [];

  return INFECTIONS.map((infection) => {
    const matched = infection.symptoms.filter((word) => {
      // Whole words for short terms, so "cut" does not fire on "cutting" and
      // "uti" does not fire on "routine".
      if (!word.includes(" ") && word.length <= 4) return haystack.includes(` ${word} `);
      return haystack.includes(word);
    });
    return { infection, matched };
  })
    .filter((r) => r.matched.length > 0)
    .sort((a, b) => b.matched.length - a.matched.length);
}

export function infectionById(id: string | null): InfectionCase | undefined {
  if (!id) return undefined;
  return INFECTIONS.find((i) => i.id === id);
}

/**
 * The choice made on the projects page has to reach step 1, which is a
 * different route and is reached after the project exists. The project id is
 * only known once the API has answered, so the hint is stored against it then.
 *
 * sessionStorage, not a URL parameter: it is a convenience, not state the user
 * should be able to share or bookmark into a confusing half-configured page.
 * Every read is defensive — if it is missing, step 1 simply shows its full
 * list of starting points, which is where the user would have been anyway.
 */
const KEY = "biostruct.infection";

export function rememberInfection(projectId: string, infectionId: string): void {
  try {
    sessionStorage.setItem(`${KEY}.${projectId}`, infectionId);
  } catch {
    // Private browsing, or storage disabled. The hint is optional by design.
  }
}

export function recallInfection(projectId: string): InfectionCase | undefined {
  try {
    return infectionById(sessionStorage.getItem(`${KEY}.${projectId}`));
  } catch {
    return undefined;
  }
}
