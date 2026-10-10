import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, describe, test } from "node:test";

import { checkInput, checkOutput } from "../../scripts/release-guard.ts";
import { CASES, FIXTURES } from "../release/cases.ts";

const temps: string[] = [];
after(() => temps.forEach((d) => rmSync(d, { recursive: true, force: true })));

function temp(): string {
  const dir = mkdtempSync(path.join(tmpdir(), "cc-release-"));
  temps.push(dir);
  return dir;
}

describe("release guard: input", () => {
  for (const [name, c] of Object.entries(CASES)) {
    test(`${name}: the guard must ${c.expect}`, () => {
      const results = temp();
      c.build(results);
      const problems = checkInput(results, FIXTURES);
      if (c.expect === "pass") {
        assert.deepEqual(problems, []);
      } else {
        assert.ok(problems.length > 0, "the guard let it through");
        assert.ok(
          problems.some((p) => c.because!.test(p)),
          `expected ${c.because}, got:\n${problems.join("\n")}`,
        );
      }
    });
  }

  test("a missing results directory fails closed", () => {
    const problems = checkInput(path.join(temp(), "results"), FIXTURES);
    assert.match(problems.join("\n"), /does not exist; a release needs at least one harness batch/);
  });

  test("the committed fixtures themselves are refused", () => {
    const problems = checkInput(FIXTURES, FIXTURES).join("\n");
    assert.match(problems, /provenance fixture, not run/);
    assert.match(problems, /holds "DEMO-"/);
    assert.match(problems, /scenario_sha256 is a demonstration scenario's/);
    assert.match(problems, /manifest_sha256 is a fixture batch's manifest/);
  });

  test("without the fixtures, the guard cannot vouch for the scenario and refuses", () => {
    const results = temp();
    CASES.clean!.build(results);
    const problems = checkInput(results, path.join(temp(), "no-fixtures"));
    assert.match(problems.join("\n"), /cannot read the fixtures/);
  });
});

describe("release guard: output", () => {
  function out(files: Record<string, string>): string {
    const dir = temp();
    for (const [rel, text] of Object.entries(files)) {
      mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
      writeFileSync(path.join(dir, rel), text);
    }
    return dir;
  }
  const page = "<!DOCTYPE html><html><body><main>Batch b-1</main></body></html>";

  test("a clean export passes, with the compare placeholder", () => {
    assert.deepEqual(
      checkOutput(
        out({
          "index.html": page,
          "compare/none/index.html": page,
          "compare/__next.compare.txt": "x",
          "_next/static/chunks/a.js": "const p=s.provenance===x",
        }),
      ),
      [],
    );
  });

  test("a DEMO- id anywhere fails", () => {
    const problems = checkOutput(out({ "trials/a/0/index.txt": '{"cell":"DEMO-W1"}' }));
    assert.match(problems.join("\n"), /trials\/a\/0\/index\.txt: holds DEMO-/);
  });

  test("the demo banner fails", () => {
    const problems = checkOutput(out({ "index.html": '<div role="note" data-demo-banner="">' }));
    assert.match(problems.join("\n"), /index\.html: holds data-demo-banner/);
  });

  test("a serialized fixture provenance fails, plain or escaped", () => {
    const plain = checkOutput(out({ "a.txt": '{"provenance":"fixture"}' }));
    const escaped = checkOutput(
      out({ "a.html": 'self.__next_f.push([1,"{\\"provenance\\":\\"fixture\\"}"])' }),
    );
    assert.equal(plain.length, 1);
    assert.equal(escaped.length, 1);
  });

  test("a compare page beyond the placeholder fails", () => {
    const problems = checkOutput(out({ "compare/abc123/index.html": page }));
    assert.match(problems.join("\n"), /compare\/abc123\/: compare is hidden in a release/);
  });
});
