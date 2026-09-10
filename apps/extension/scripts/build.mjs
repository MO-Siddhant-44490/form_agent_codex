// Bundles the extension into dist/ with esbuild.
//
//   node scripts/build.mjs          # committed manifest (localhost only)
//   node scripts/build.mjs --dev    # dist/manifest.json widened to <all_urls>
//                                   # for trying the extension on any site
//
// --dev only ever rewrites the copy in dist/; the source manifest is untouched.
import { build } from "esbuild";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const dev = process.argv.includes("--dev");

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });

const common = { bundle: true, minify: false, sourcemap: false, target: "chrome116", logLevel: "error" };

await Promise.all([
  build({ ...common, entryPoints: [join(root, "src/background/index.ts")], outfile: join(dist, "background.js"), format: "esm" }),
  build({ ...common, entryPoints: [join(root, "src/content/index.ts")], outfile: join(dist, "content.js"), format: "iife" }),
  build({ ...common, entryPoints: [join(root, "src/sidepanel/index.ts")], outfile: join(dist, "sidepanel.js"), format: "iife" }),
]);

await cp(join(root, "manifest.json"), join(dist, "manifest.json"));
await cp(join(root, "src/sidepanel/sidepanel.html"), join(dist, "sidepanel.html"));

if (dev) {
  const manifestPath = join(dist, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.host_permissions = ["<all_urls>"];
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
}
console.log(`extension built into dist/${dev ? " (dev: host_permissions <all_urls>)" : ""}`);
