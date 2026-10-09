// Validates every artifact the build will read against schemas/v1 (runs before `next build`).

import { artifactRoot, validateArtifactTree } from "../lib/artifacts.ts";

const root = artifactRoot();
if (root === null) {
  console.log(
    "validate-artifacts: no artifact directory (set CRASHCOURT_ARTIFACTS); nothing to read",
  );
} else {
  try {
    const { files, events, manifests, batches, results } = validateArtifactTree(root);
    console.log(
      `validate-artifacts: ${files} files in ${root}: ${events} events, ${manifests} manifests, ` +
        `${batches} batches, ${results} results, all valid`,
    );
  } catch (e) {
    console.error(`validate-artifacts: ${root}\n${(e as Error).message}`);
    process.exit(1);
  }
}
