import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { validateArtifactTree } from "../../lib/artifacts.ts";
import {
  ArtifactError,
  isDate,
  parseBatch,
  parseEvents,
  parseManifest,
  parseResult,
} from "../../lib/schema/validate.ts";

const REPO = fileURLToPath(new URL("../../../", import.meta.url));
const SAMPLE = path.join(REPO, "research/evidence/t-04/sample");

const historyFiles = readdirSync(SAMPLE).filter((f) => f.endsWith(".jsonl"));
// The reference manifest CI compares against is the one recorded under the current v1; earlier
// tasks' references predate fields v1 requires and stay as records of their environments.
const workflow = readFileSync(path.join(REPO, ".github/workflows/ci.yml"), "utf8");
const compared = /--compare\s+(\S+)/.exec(workflow)?.[1];
if (!compared) throw new Error("ci.yml names no --compare reference");
const manifestFiles = [path.join(REPO, compared)];

const firstLine = readFileSync(path.join(SAMPLE, "merged.jsonl"), "utf8").split("\n")[0]!;
const goodEvent = JSON.parse(firstLine) as Record<string, unknown>;
const goodManifest = JSON.parse(readFileSync(manifestFiles[0]!, "utf8")) as Record<string, unknown>;

function eventLine(change: (e: Record<string, unknown>) => void): string {
  const e = structuredClone(goodEvent);
  change(e);
  return JSON.stringify(e);
}

function rejects(fn: () => unknown, pattern: RegExp): void {
  assert.throws(fn, (e: unknown) => {
    assert.ok(e instanceof ArtifactError, `expected an ArtifactError, got ${String(e)}`);
    assert.match(e.message, pattern);
    return true;
  });
}

describe("events", () => {
  test("every T-04 sample history validates", () => {
    assert.ok(historyFiles.length >= 2, "the T-04 sample has histories");
    for (const f of historyFiles) {
      const text = readFileSync(path.join(SAMPLE, f), "utf8");
      const events = parseEvents(text, f);
      assert.equal(events.length, text.trimEnd().split("\n").length, f);
    }
  });

  test("nanosecond clocks come back exact", () => {
    const [event] = parseEvents(firstLine, "merged.jsonl");
    const wall = /"wall_ns":(\d+)/.exec(firstLine)![1]!;
    const mono = /"mono_ns":(\d+)/.exec(firstLine)![1]!;
    assert.equal(event!.wall_ns, BigInt(wall));
    assert.equal(event!.mono_ns, BigInt(mono));
    const big = eventLine((e) => (e.wall_ns = 0)).replace(
      '"wall_ns":0',
      '"wall_ns":1790000000123456789',
    );
    assert.equal(parseEvents(big, "x")[0]!.wall_ns, 1790000000123456789n);
    // A monotonic clock past 2^53 (a host up for about 104 days) must come back exact too.
    const late = eventLine((e) => (e.mono_ns = 0)).replace(
      '"mono_ns":0',
      '"mono_ns":9007199254740993',
    );
    assert.equal(parseEvents(late, "x")[0]!.mono_ns, 9007199254740993n);
  });

  const malformed: [string, string, RegExp][] = [
    [
      "a required field is missing",
      eventLine((e) => delete e.kind),
      /must have required property 'kind'/,
    ],
    ["seq is a string", eventLine((e) => (e.seq = "0")), /\/seq must be integer/],
    ["seq is negative", eventLine((e) => (e.seq = -1)), /\/seq must be >= 0/],
    [
      "the kind is unknown",
      eventLine((e) => (e.kind = "saver.put.maybe")),
      /\/kind must be equal to one of the allowed values/,
    ],
    [
      "a field is not in the schema",
      eventLine((e) => (e.note = "x")),
      /must NOT have additional properties 'note'/,
    ],
    ["the schema version is wrong", eventLine((e) => (e.v = 2)), /\/v must be equal to constant/],
    [
      "the batch id is empty",
      eventLine((e) => (e.batch = "")),
      /\/batch must NOT have fewer than 1 characters/,
    ],
    ["the status is unknown", eventLine((e) => (e.status = "done")), /\/status/],
    ["attempts start at 1", eventLine((e) => (e.attempt = 0)), /\/attempt/],
    ["data is not an object", eventLine((e) => (e.data = [])), /\/data must be object/],
    ["a clock is fractional", eventLine((e) => (e.mono_ns = 1.5)), /\/mono_ns must be integer/],
    ["a line is truncated", firstLine.slice(0, 40), /not valid JSON/],
  ];
  for (const [what, line, pattern] of malformed) {
    test(`rejects an event where ${what}`, () =>
      rejects(() => parseEvents(line, "h.jsonl"), pattern));
  }

  test("rejects a clock written in exponent form, which would lose its digits", () => {
    const line = eventLine((e) => (e.wall_ns = 0)).replace('"wall_ns":0', '"wall_ns":1.79e18');
    rejects(() => parseEvents(line, "h.jsonl"), /wall_ns must be a JSON integer literal/);
  });

  test("names the file and line of the first bad event", () => {
    const text = `${firstLine}\n${eventLine((e) => (e.seq = "x"))}\n`;
    rejects(() => parseEvents(text, "worker-1.jsonl"), /^worker-1\.jsonl:2: /);
  });

  test("rejects a blank line inside a history", () => {
    rejects(
      () => parseEvents(`${firstLine}\n\n${firstLine}\n`, "h.jsonl"),
      /h\.jsonl:2: not valid JSON/,
    );
  });
});

describe("manifests", () => {
  test("the reference manifest CI compares against validates", () => {
    for (const f of manifestFiles) parseManifest(readFileSync(f, "utf8"), f);
  });

  const malformed: [string, (m: Record<string, unknown>) => void, RegExp][] = [
    [
      "the lockfile hash is missing",
      (m) => delete m.lockfile_sha256,
      /required property 'lockfile_sha256'/,
    ],
    [
      "the schema version is 2",
      (m) => (m.schema_version = 2),
      /\/schema_version must be equal to constant/,
    ],
    [
      "a key is not in the schema",
      (m) => (m.provenance = "run"),
      /additional properties 'provenance'/,
    ],
    [
      "a package version is a number",
      (m) => ((m.packages as Record<string, unknown>).langgraph = 1),
      /\/packages\/langgraph must be string/,
    ],
    ["git_dirty is a string", (m) => (m.git_dirty = "false"), /\/git_dirty must be boolean/],
  ];
  for (const [what, change, pattern] of malformed) {
    test(`rejects a manifest where ${what}`, () => {
      const m = structuredClone(goodManifest);
      change(m);
      rejects(() => parseManifest(JSON.stringify(m), "manifest.json"), pattern);
    });
  }
});

const DIGEST = "a".repeat(64);
const SCENARIO = "b".repeat(64);
const goodBatch = {
  v: 1,
  batch: "DEMO-b",
  provenance: "fixture",
  manifest_sha256: DIGEST,
  scenario_sha256: SCENARIO,
};
const goodResult = {
  v: 1,
  batch: "DEMO-b",
  cell: "DEMO-c",
  trial: 0,
  provenance: "fixture",
  manifest_sha256: DIGEST,
  results: [
    { invariant: "I1", outcome: "NO_ANOMALY_UNSPECIFIED" },
    {
      invariant: "I2",
      outcome: "VIOLATION_CANDIDATE",
      clause: "LG-DUR-SYNC",
      evidence: ["trials/0/merged.jsonl#/41"],
    },
    {
      invariant: "I3",
      outcome: "VIOLATION",
      clause: "LG-DUR-SYNC",
      evidence: ["trials/0/merged.jsonl#/41"],
      repro: "repros/F-0001",
      review: { reviewer: "maintainer", reviewed: "2026-10-08" },
    },
  ],
};

describe("records", () => {
  test("a batch and a result with every outcome shape validate", () => {
    assert.equal(parseBatch(JSON.stringify(goodBatch), "batch.json").provenance, "fixture");
    const result = parseResult(JSON.stringify(goodResult), "result.json");
    assert.deepEqual(
      result.results.map((r) => r.outcome),
      goodResult.results.map((r) => r.outcome),
    );
  });

  const badBatches: [string, Record<string, unknown>, RegExp][] = [
    ["provenance is unknown", { ...goodBatch, provenance: "demo" }, /\/provenance/],
    [
      "the manifest hash is not sha256",
      { ...goodBatch, manifest_sha256: "ABC" },
      /manifest_sha256/,
    ],
    ["a key is not in the schema", { ...goodBatch, note: "x" }, /additional properties 'note'/],
    ["the batch id has whitespace", { ...goodBatch, batch: "b 1" }, /\/batch must match pattern/],
  ];
  for (const [what, value, pattern] of badBatches) {
    test(`rejects a batch where ${what}`, () =>
      rejects(() => parseBatch(JSON.stringify(value), "batch.json"), pattern));
  }

  const result = (change: (r: Record<string, unknown>) => void): string => {
    const r = structuredClone(goodResult) as unknown as Record<string, unknown>;
    change(r);
    return JSON.stringify(r);
  };
  const first = (r: Record<string, unknown>): Record<string, unknown> =>
    (r.results as Record<string, unknown>[])[0]!;
  const badResults: [string, string, RegExp][] = [
    ["there are no outcomes", result((r) => (r.results = [])), /\/results must NOT have fewer/],
    [
      "an outcome is not one of the seven",
      result((r) => (first(r).outcome = "MAYBE")),
      /\/results\/0\/outcome must be equal to one of the allowed values/,
    ],
    [
      "a review date is not a real day",
      result((r) => {
        const last = (r.results as Record<string, unknown>[])[2]!;
        last.review = { reviewer: "maintainer", reviewed: "2026-02-30" };
      }),
      /\/results\/2\/review\/reviewed must match format "date"/,
    ],
    ["the trial is negative", result((r) => (r.trial = -1)), /\/trial must be >= 0/],
  ];
  for (const [what, text, pattern] of badResults) {
    test(`rejects a result where ${what}`, () =>
      rejects(() => parseResult(text, "result.json"), pattern));
  }

  test("a date names a real calendar day", () => {
    assert.ok(isDate("2024-02-29"));
    assert.ok(isDate("0050-01-01"), "years below 100 are real years, as Pydantic writes them");
    for (const bad of ["2023-02-29", "2026-13-01", "2026-1-01", "26-01-01", "2026-01-01T00:00"]) {
      assert.equal(isDate(bad), false, bad);
    }
  });
});

describe("artifact tree", () => {
  function tree(files: Record<string, string>): string {
    const root = mkdtempSync(path.join(tmpdir(), "cc-artifacts-"));
    for (const [name, content] of Object.entries(files)) {
      mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
      writeFileSync(path.join(root, name), content);
    }
    return root;
  }

  test("counts what it validated", () => {
    const root = tree({
      "b/manifest.json": JSON.stringify(goodManifest),
      "b/cell/0/worker-1.jsonl": `${firstLine}\n${firstLine}\n`,
      "b/batch.json": JSON.stringify(goodBatch),
      "b/cell/0/result.json": JSON.stringify(goodResult),
    });
    try {
      assert.deepEqual(validateArtifactTree(root), {
        files: 4,
        events: 2,
        manifests: 1,
        batches: 1,
        results: 1,
      });
    } finally {
      rmSync(root, { recursive: true });
    }
  });

  test("reports every bad or unrecognized file, not just the first", () => {
    const root = tree({
      "b/manifest.json": "{}",
      "b/cell/0/worker-1.jsonl": eventLine((e) => (e.kind = "nope")),
      "b/notes.txt": "hello",
    });
    try {
      assert.throws(
        () => validateArtifactTree(root),
        (e: unknown) => {
          assert.ok(e instanceof AggregateError);
          assert.equal(e.errors.length, 3);
          assert.match(e.message, /no schema covers this file/);
          return true;
        },
      );
    } finally {
      rmSync(root, { recursive: true });
    }
  });
});

interface RecordCase {
  readonly name: string;
  readonly kind: "batch" | "result";
  readonly valid: boolean;
  readonly record: unknown;
}
const SHARED = JSON.parse(readFileSync(path.join(REPO, "tests/data/records-v1.json"), "utf8")) as {
  cases: RecordCase[];
};

describe("records decided as the harness's models decide them (tests/data/records-v1.json)", () => {
  test("the shared cases are there", () => assert.ok(SHARED.cases.length >= 30));
  for (const c of SHARED.cases) {
    test(`${c.valid ? "accepts" : "rejects"}: ${c.name}`, () => {
      const parse = c.kind === "batch" ? parseBatch : parseResult;
      const run = (): unknown => parse(JSON.stringify(c.record), `${c.kind}.json`);
      if (c.valid) run();
      else assert.throws(run, ArtifactError);
    });
  }
});
