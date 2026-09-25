// Predicted page quality: htrflow's QualityPrediction step scores each
// page 0-1 (a predicted bag-of-words F1). No colour and no threshold here:
// what counts as poor depends on the material, and is not this page's call.

export function formatQuality(q: number | null | undefined): string {
  return typeof q === "number" && Number.isFinite(q) ? q.toFixed(2) : "";
}

/** htrflow resolves a step by its lower-cased name, so this does too. */
export function hasQualityStep(steps: readonly string[]): boolean {
  return steps.some((s) => s.toLowerCase() === "qualityprediction");
}

/** Worst first; a page with no score after every scored one; ties by id. */
export function sortByQuality<
  T extends { id: string; quality?: number | undefined },
>(rows: readonly T[]): T[] {
  return [...rows].sort((a, b) => {
    const qa = a.quality ?? Number.POSITIVE_INFINITY;
    const qb = b.quality ?? Number.POSITIVE_INFINITY;
    return qa !== qb ? qa - qb : a.id.localeCompare(b.id);
  });
}
