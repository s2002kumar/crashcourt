// Validates every artifact the build will read against schemas/v1, and the rules that span files
// (lib/batches.ts), before `next build`.

import { artifactRoot, validateArtifactTree } from "../lib/artifacts.ts";
import { loadBatches } from "../lib/batches.ts";

const root = artifactRoot();
if (root === null) {
  console.log(
    "validate-artifacts: no artifact directory (set CRASHCOURT_ARTIFACTS); nothing to read",
  );
} else {
  try {
    const { files, events, manifests, batches, results } = validateArtifactTree(root);
    const loaded = loadBatches(root);
    console.log(
      `validate-artifacts: ${files} files in ${root}: ${events} events, ${manifests} manifests, ` +
        `${batches} batches, ${results} results, all valid; ${loaded.trials.length} trials in ` +
        `${loaded.batches.length} batches agree with each other`,
    );
  } catch (e) {
    console.error(`validate-artifacts: ${root}\n${(e as Error).message}`);
    process.exit(1);
  }
}
