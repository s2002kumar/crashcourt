// Generates lib/schema/types.gen.ts (TypeScript types) and lib/schema/schemas.gen.ts (the JSON Schemas
// as modules, for Ajv) from the frozen schemas in ../schemas/v1. The Python models are the source;
// `crashcourt schema export` writes schemas/v1, and this script carries them into the site.
//
//   node scripts/gen-schema.ts           write the generated files
//   node scripts/gen-schema.ts --check   exit 1 if they are out of date

import { readFileSync, writeFileSync } from "node:fs";
import { compile, type JSONSchema } from "json-schema-to-typescript";
import { format, resolveConfig } from "prettier";

const SCHEMAS = [
  { file: "event.json", type: "Event", constName: "eventSchema" },
  { file: "manifest.json", type: "Manifest", constName: "manifestSchema" },
] as const;

const SOURCE = new URL("../../schemas/v1/", import.meta.url);
const TARGET = new URL("../lib/schema/", import.meta.url);
const BANNER = [
  "// Generated from schemas/v1 by scripts/gen-schema.ts. Do not edit; run `npm run gen:schema`.",
  "",
].join("\n");

/** Property titles would become one named type per field; the types read better inlined. */
function withoutPropertyTitles(schema: JSONSchema): JSONSchema {
  const copy = structuredClone(schema);
  for (const prop of Object.values(copy.properties ?? {})) delete prop.title;
  return copy;
}

async function prettify(source: string, file: URL): Promise<string> {
  const options = (await resolveConfig(file)) ?? {};
  return format(source, { ...options, parser: "typescript" });
}

export async function generate(): Promise<Map<URL, string>> {
  const types: string[] = [];
  const modules: string[] = [];
  for (const { file, type, constName } of SCHEMAS) {
    const schema = JSON.parse(readFileSync(new URL(file, SOURCE), "utf8")) as JSONSchema;
    types.push(
      await compile(withoutPropertyTitles(schema), type, {
        bannerComment: "",
        format: false,
        additionalProperties: false,
      }),
    );
    modules.push(
      `/** schemas/v1/${file} */\nexport const ${constName}: Record<string, unknown> = ${JSON.stringify(schema)};`,
    );
  }
  const typesUrl = new URL("types.gen.ts", TARGET);
  const schemasUrl = new URL("schemas.gen.ts", TARGET);
  return new Map([
    [typesUrl, await prettify(BANNER + types.join("\n"), typesUrl)],
    [schemasUrl, await prettify(BANNER + modules.join("\n\n"), schemasUrl)],
  ]);
}

if (import.meta.main) {
  const check = process.argv.includes("--check");
  const stale: string[] = [];
  for (const [url, content] of await generate()) {
    let current: string | undefined;
    try {
      current = readFileSync(url, "utf8");
    } catch {
      current = undefined;
    }
    if (current === content) continue;
    if (check) stale.push(url.pathname);
    else writeFileSync(url, content);
  }
  if (stale.length > 0) {
    console.error(`out of date (run npm run gen:schema):\n  ${stale.join("\n  ")}`);
    process.exit(1);
  }
  console.log(check ? "generated schema files are up to date" : "wrote lib/schema/*.gen.ts");
}
