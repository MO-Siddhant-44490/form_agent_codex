// Generates Zod validators + TS types in src/generated/ from the JSON Schemas
// exported by the Pydantic contracts (the source of truth). CI fails on drift.
import $RefParser from "@apidevtools/json-schema-ref-parser";
import { jsonSchemaToZod } from "json-schema-to-zod";
import { readFileSync, writeFileSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { dirname, join, basename } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const schemaDir = join(here, "..", "..", "schema");
const outDir = join(here, "..", "src", "generated");

const manifest = JSON.parse(readFileSync(join(schemaDir, "manifest.json"), "utf8"));

function pascalCase(snake) {
  return snake
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

const indexLines = [
  "// AUTO-GENERATED from packages/contracts/schema — do not edit by hand.",
  `// Protocol version: ${manifest.protocol_version}`,
  `export const PROTOCOL_VERSION = ${JSON.stringify(manifest.protocol_version)};`,
];

for (const file of manifest.schemas) {
  const name = pascalCase(basename(file, ".json"));
  const raw = JSON.parse(readFileSync(join(schemaDir, file), "utf8"));
  // Inline internal $refs; json-schema-to-zod does not resolve $defs itself.
  const schema = await $RefParser.dereference(raw, { mutateInputSchema: false });
  delete schema.$defs;
  const zodCode = jsonSchemaToZod(schema, {
    name: `${name}Schema`,
    module: "esm",
    type: name,
  });
  const header = "// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/" + file + "\n";
  writeFileSync(join(outDir, `${name}.ts`), header + zodCode);
  indexLines.push(`export { ${name}Schema, type ${name} } from "./${name}";`);
}

writeFileSync(join(outDir, "index.ts"), indexLines.join("\n") + "\n");
console.log(`generated ${manifest.schemas.length} validators into src/generated`);
