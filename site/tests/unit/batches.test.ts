import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { artifactRoot, validateArtifactTree } from "../../lib/artifacts.ts";
import {
  comparisons,
  findings,
  latestBatch,
  loadBatches,
  threadsOf,
  trialKey,
} from "../../lib/batches.ts";
import { combined, showsFixture } from "../../lib/provenance.ts";
import { firstPages, findingParams, lineageParams, trialParams } from "../../lib/routes.ts";

const SITE = fileURLToPath(new URL("../..", import.meta.url));
const FIXTURES = path.join(SITE, "fixtures");
const temps: string[] = [];
after(() => temps.forEach((d) => rmSync(d, { recursive: true, force: true })));

function copyOfFixtures(): string {
  const dir = mkdtempSync(path.join(tmpdir(), "cc-batches-"));
  temps.push(dir);
  cpSync(FIXTURES, dir, { recursive: true });
  return dir;
}

function withEnv<T>(name: string, value: string | undefined, fn: () => T): T {
  const before = process.env[name];
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
  try {
    return fn();
  } finally {
    if (before === undefined) delete process.env[name];
    else process.env[name] = before;
  }
}

function rejects(root: string, pattern: RegExp): void {
  assert.throws(
    () => loadBatches(root),
    (e: unknown) => {
      assert.ok(e instanceof AggregateError, String(e));
      assert.match(e.message, pattern);
      return true;
    },
  );
}

describe("the demo fixtures", () => {
  const all = loadBatches(FIXTURES);

  test("pass the Ajv validation the build runs", () => {
    const summary = validateArtifactTree(FIXTURES);
    assert.equal(summary.batches, 3);
    assert.equal(summary.manifests, 3);
    assert.ok(summary.results >= 20, `${summary.results} results`);
    assert.ok(summary.events > 1000, `${summary.events} events`);
  });

  test("load as three batches whose records and trials are all fixtures", () => {
    assert.deepEqual(
      all.batches.map((b) => b.id),
      ["DEMO-batch-compare-a", "DEMO-batch-compare-b", "DEMO-batch-matrix"],
    );
    for (const s of [...all.batches, ...all.trials]) assert.equal(s.provenance, "fixture");
    assert.ok(all.trials.every((t) => t.merged !== null && t.histories.length >= 3));
  });

  test("are what a dev build reads when nothing else is configured", () => {
    withEnv("CRASHCOURT_RELEASE", undefined, () =>
      withEnv("CRASHCOURT_ARTIFACTS", undefined, () => assert.equal(artifactRoot(SITE), FIXTURES)),
    );
  });

  test("give every dynamic route its ids", () => {
    assert.equal(trialParams(all).length, all.trials.length);
    assert.equal(lineageParams(all).length, all.trials.length); // one thread per demo trial
    assert.deepEqual(findingParams(all), [{ id: "DEMO-repro-W1-F08-orphaned-effect" }]);
    const pages = firstPages(all);
    const first = all.trials[0]!;
    assert.equal(pages["/trials/[cell]/[trial]"], `/trials/${first.cell}/${first.trial}/`);
    assert.equal(
      pages["/lineage/[trial]/[thread]"],
      `/lineage/${trialKey(first)}/${threadsOf(first)[0]}/`,
    );
  });

  test("name the matrix batch the latest run, and one finding seen in three trials", () => {
    assert.equal(latestBatch(all)?.id, "DEMO-batch-matrix");
    const [finding] = findings(all);
    assert.equal(finding?.occurrences.length, 3);
    assert.equal(finding?.provenance, "fixture");
  });

  test("offer a comparison in a dev build and none in a release build", () => {
    const dev = withEnv("CRASHCOURT_RELEASE", undefined, () => comparisons(all));
    assert.equal(dev.length, 1);
    assert.deepEqual(
      dev[0]!.batches.map((b) => b.id),
      ["DEMO-batch-compare-a", "DEMO-batch-compare-b"],
    );
    assert.deepEqual(
      withEnv("CRASHCOURT_RELEASE", "1", () => comparisons(all)),
      [],
    );
  });
});

describe("a release build", () => {
  test("reads ../results and refuses to be pointed anywhere else", () => {
    withEnv("CRASHCOURT_RELEASE", "1", () => {
      withEnv("CRASHCOURT_ARTIFACTS", undefined, () =>
        assert.equal(artifactRoot(SITE), path.resolve(SITE, "..", "results")),
      );
      withEnv("CRASHCOURT_ARTIFACTS", "fixtures", () =>
        assert.throws(() => artifactRoot(SITE), /reads \.\.\/results only/),
      );
    });
  });
});

describe("the cross-file rules", () => {
  const trialDir = (root: string) =>
    path.join(root, "DEMO-batch-matrix", "DEMO-pg-async-sync-W1-F08", "0");

  test("a batch's manifest must hash to its manifest_sha256", () => {
    const root = copyOfFixtures();
    const manifest = path.join(root, "DEMO-batch-matrix", "manifest.json");
    writeFileSync(manifest, readFileSync(manifest, "utf8").replace('"local"', '"self-hosted"'));
    rejects(root, /manifest_sha256 is not the sha256 of DEMO-batch-matrix\/manifest\.json/);
  });

  test("a result must name its batch and its batch's manifest hash", () => {
    const root = copyOfFixtures();
    const file = path.join(trialDir(root), "result.json");
    const result = JSON.parse(readFileSync(file, "utf8")) as Record<string, unknown>;
    writeFileSync(
      file,
      JSON.stringify({ ...result, batch: "DEMO-batch-other", manifest_sha256: "0".repeat(64) }),
    );
    rejects(root, /names batch DEMO-batch-other, not DEMO-batch-matrix/);
    rejects(root, /manifest_sha256 differs from its batch's/);
  });

  test("every event must belong to its trial", () => {
    const root = copyOfFixtures();
    const file = path.join(trialDir(root), "worker-a.jsonl");
    writeFileSync(file, readFileSync(file, "utf8").replaceAll('"trial":0', '"trial":7'));
    rejects(root, /worker-a\.jsonl: .*an event of DEMO-batch-matrix DEMO-pg-async-sync-W1-F08 #7/);
  });

  test("a history must sit in a trial directory, and a batch needs its two records", () => {
    const root = copyOfFixtures();
    writeFileSync(
      path.join(root, "DEMO-batch-matrix", "stray.jsonl"),
      readFileSync(path.join(trialDir(root), "sink.jsonl")),
    );
    rmSync(path.join(root, "DEMO-batch-compare-b", "manifest.json"));
    rejects(root, /stray\.jsonl: .*outside any trial directory/);
    rejects(root, /DEMO-batch-compare-b: .*holds batch\.json and manifest\.json/);
  });

  test("two trials cannot share a page", () => {
    const root = copyOfFixtures();
    cpSync(
      path.join(root, "DEMO-batch-matrix", "DEMO-pg-async-sync-W1-F08"),
      path.join(root, "DEMO-batch-compare-a", "DEMO-pg-async-sync-W1-F08"),
      { recursive: true },
    );
    rejects(root, /\/trials\/DEMO-pg-async-sync-W1-F08\/0\/ would show both/);
  });

  test("a file outside any batch directory is refused", () => {
    const root = copyOfFixtures();
    writeFileSync(path.join(root, "result.json"), "{}");
    rejects(root, /result\.json: .*outside any batch directory/);
  });
});

describe("provenance", () => {
  test("a page shows demo data when any source is a fixture", () => {
    assert.equal(showsFixture([]), false);
    assert.equal(showsFixture([{ provenance: "run" }, { provenance: "run" }]), false);
    assert.equal(showsFixture([{ provenance: "run" }, { provenance: "fixture" }]), true);
    assert.equal(combined([{ provenance: "run" }, { provenance: "fixture" }]), "fixture");
    assert.equal(combined([{ provenance: "run" }]), "run");
  });
});
