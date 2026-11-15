/**
 * Sanity checks on a protein somebody is about to attack.
 *
 * The search box talks to UniProt, which holds every known protein from every
 * organism. Searching "bacteria" returns 25 entries that merely mention the
 * word, and the pipeline would happily accept any of them: pick a site, design
 * ten binders, fold them, rank them, and hand back a confident result that
 * means nothing. Nothing in the machine objects, because nothing in the
 * machine knows what the user was trying to do.
 *
 * So the interface has to object. These checks are deliberately shallow —
 * they read the name, the function text and the gene names — and they are
 * stated as "this does not look like" rather than "this is wrong", because a
 * shallow check has no business blocking a specialist who knows better.
 */

/**
 * Words that appear in the name or description of proteins that cause
 * antibiotic resistance. Kept explicit rather than clever: anybody can read
 * this list and know exactly why a result was or was not flagged.
 */
const RESISTANCE_TERMS: { term: string; plain: string }[] = [
  { term: "lactamase", plain: "it is a beta-lactamase, the commonest cause of resistance" },
  { term: "carbapenemase", plain: "it destroys carbapenems, our last-resort antibiotics" },
  { term: "penicillinase", plain: "it destroys penicillins" },
  { term: "cephalosporinase", plain: "it destroys cephalosporins" },
  { term: "oxacillinase", plain: "it destroys oxacillin-type antibiotics" },
  { term: "penicillin-binding", plain: "it is a penicillin-binding protein, a classic drug target" },
  { term: "efflux", plain: "it pumps antibiotics back out of the cell" },
  { term: "multidrug", plain: "it is involved in resistance to several drugs at once" },
  { term: "antibiotic resistance", plain: "its description says antibiotic resistance" },
  { term: "drug resistance", plain: "its description says drug resistance" },
  { term: "resistance protein", plain: "its description calls it a resistance protein" },
  { term: "aminoglycoside", plain: "it defeats aminoglycoside antibiotics" },
  { term: "chloramphenicol", plain: "it defeats chloramphenicol" },
  { term: "tetracycline", plain: "it defeats tetracyclines" },
  { term: "vancomycin", plain: "it defeats vancomycin" },
  { term: "methicillin", plain: "it defeats methicillin" },
  { term: "macrolide", plain: "it defeats macrolide antibiotics" },
  { term: "quinolone", plain: "it defeats quinolone antibiotics" },
  { term: "rifampicin", plain: "it defeats rifampicin" },
  { term: "sulfonamide", plain: "it defeats sulfonamides" },
  { term: "trimethoprim", plain: "it defeats trimethoprim" },
];

export type TargetJudgement = {
  /** Did anything mark this out as a resistance protein? */
  looksRelevant: boolean;
  /** The plain reason, when it did. */
  because: string | null;
  /** Things that will make the next steps hard or impossible. */
  warnings: string[];
};

export function judgeTarget(c: {
  protein_name: string;
  function?: string;
  gene_names?: string[];
  pdb_count: number;
  active_sites: unknown[];
}): TargetJudgement {
  const haystack = [c.protein_name, c.function ?? "", ...(c.gene_names ?? [])]
    .join(" ")
    .toLowerCase();

  const hit = RESISTANCE_TERMS.find((t) => haystack.includes(t.term));

  const warnings: string[] = [];
  if (c.pdb_count === 0) {
    // Step 2 starts from a measured structure. Without one there is nothing
    // to pick an attack site on, and the user would have to supply a PDB
    // code by hand — which is exactly the knowledge this product exists to
    // not require.
    warnings.push(
      "No measured 3D shape exists for this protein, so there is nothing to pick an attack site on. You would have to supply one yourself.",
    );
  }
  if (c.active_sites.length === 0) {
    warnings.push(
      "Nobody has recorded where its working parts are, so the attack site will not be filled in for you.",
    );
  }

  return {
    looksRelevant: Boolean(hit),
    because: hit?.plain ?? null,
    warnings,
  };
}

/**
 * Very common words that match thousands of unrelated UniProt entries. Typing
 * one is not an error, but the results are close to meaningless, and saying so
 * is kinder than letting somebody pick the third row of noise.
 */
const TOO_BROAD = [
  "bacteria",
  "bacterial",
  "bacterium",
  "protein",
  "proteins",
  "enzyme",
  "enzymes",
  "infection",
  "disease",
  "antibiotic",
  "antibiotics",
  "resistance",
  "drug",
  "drugs",
  "virus",
  "cell",
  "gene",
];

export function isTooBroad(query: string): boolean {
  const words = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  return words.length > 0 && words.every((w) => TOO_BROAD.includes(w));
}
