// Bundles the extension into dist/ with esbuild.
import { build } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");

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
console.log("extension built into dist/");
