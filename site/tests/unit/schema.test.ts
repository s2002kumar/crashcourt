import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { validateArtifactTree } from "../../lib/artifacts.ts";
import { ArtifactError, parseEvents, parseManifest } from "../../lib/schema/validate.ts";

const REPO = fileURLToPath(new URL("../../../", import.meta.url));
const SAMPLE = path.join(REPO, "research/evidence/t-04/sample");

const historyFiles = readdirSync(SAMPLE).filter((f) => f.endsWith(".jsonl"));
const manifestFiles = readdirSync(path.join(REPO, "research/evidence"))
  .map((d) => path.join(REPO, "research/evidence", d, "manifest-local.json"))
  .filter((f) => {
    try {
      readFileSync(f);
      return true;
    } catch {
      return false;
    }
  });

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
  test("every committed reference manifest validates", () => {
    assert.ok(manifestFiles.length >= 2);
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
    });
    try {
      assert.deepEqual(validateArtifactTree(root), { files: 2, events: 2, manifests: 1 });
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
