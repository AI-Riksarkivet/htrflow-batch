// The models a campaign's pipeline runs, read off `JobDetail.pipelineYaml`
// for the card's Models line.
//
// No YAML library: this document is small, known and written by us (the
// converter renders it into the `htr-pipeline-<id>` ConfigMap), and only two
// keys are wanted from it. A model is pinned in one of exactly two places —
// `model_settings.revision` (YOLO's Ultralytics wrapper) or
// `model_settings.model_kwargs.revision` (TrOCR, Donut, DiT, forwarded to
// `from_pretrained`) — the same two the cluster's Kyverno model-revision
// policy accepts (charts/htrflow-batch/templates/policies/model-revision.yaml).
import { isHttpUrl } from "./api.js";

/** Where a Hugging Face repo id resolves. */
export const HF_BASE = "https://huggingface.co";

export type PipelineModel = {
  /** Hugging Face repo id, e.g. `Riksarkivet/yolov9-regions-1`. */
  id: string;
  /** The commit it is pinned to, or null when the pipeline pins nothing. */
  revision: string | null;
};

type Entry = { indent: number; key: string; value: string };

/** Every `key: value` line, with its indent; comments and blanks dropped. */
function entries(yaml: string): Entry[] {
  const out: Entry[] = [];
  for (const raw of yaml.split("\n")) {
    // A list marker indents the mapping it opens exactly as two spaces would.
    const match = /^(\s*)([\w.-]+):\s*(.*)$/.exec(
      raw.replace(/^(\s*)-\s/, "$1  "),
    );
    if (match !== null)
      out.push({
        indent: match[1].length,
        key: match[2],
        value: match[3].trim().replace(/^["']|["']$/g, ""),
      });
  }
  return out;
}

/** One entry per `model_settings` block that names a model, in step order. */
export function pipelineModels(yaml: string): PipelineModel[] {
  const rows = entries(yaml);
  const models: PipelineModel[] = [];
  rows.forEach((row, i) => {
    if (row.key !== "model_settings") return;
    const block: Entry[] = [];
    for (let j = i + 1; j < rows.length && rows[j].indent > row.indent; j++)
      block.push(rows[j]);
    const id = block.find((e) => e.key === "model" && e.value !== "");
    // Either placement: nothing else inside a model_settings block carries a
    // `revision`, so the first one found is the model's, wherever it sits.
    const revision = block.find((e) => e.key === "revision" && e.value !== "");
    if (id !== undefined)
      models.push({ id: id.value, revision: revision?.value ?? null });
  });
  return models;
}

/** `yolov9-regions-1 @6fb01d2`, or `… unpinned` when nothing pins it. */
export function modelLabel(model: PipelineModel): string {
  const name = model.id.split("/").pop() ?? model.id;
  return model.revision === null
    ? `${name} unpinned`
    : `${name} @${model.revision.slice(0, 7)}`;
}

/**
 * The repo's file tree at that commit — `main` for an unpinned model. Built
 * from the constant base and an escaped id, then guarded like every other
 * href on this page: the YAML is a file humans edit in a git repo.
 */
export function modelUrl(model: PipelineModel): string | null {
  const path = model.id.split("/").map(encodeURIComponent).join("/");
  const url = `${HF_BASE}/${path}/tree/${encodeURIComponent(model.revision ?? "main")}`;
  return isHttpUrl(url) ? url : null;
}
