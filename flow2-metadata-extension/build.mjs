import { build } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";

const root = import.meta.dirname;
const dist = resolve(root, "dist");

await rm(dist, { recursive: true, force: true });
await mkdir(resolve(dist, "icons"), { recursive: true });
await mkdir(resolve(dist, "fonts"), { recursive: true });

await Promise.all([
  cp(resolve(root, "static/manifest.json"), resolve(dist, "manifest.json")),
  cp(resolve(root, "static/popup.html"), resolve(dist, "popup.html")),
  cp(resolve(root, "static/popup.css"), resolve(dist, "popup.css")),
  cp(resolve(root, "static/icons"), resolve(dist, "icons"), { recursive: true }),
  cp(resolve(root, "node_modules/@fontsource/manrope/files/manrope-latin-400-normal.woff2"), resolve(dist, "fonts/manrope-400.woff2")),
  cp(resolve(root, "node_modules/@fontsource/manrope/files/manrope-latin-600-normal.woff2"), resolve(dist, "fonts/manrope-600.woff2")),
  cp(resolve(root, "node_modules/@fontsource/manrope/files/manrope-latin-700-normal.woff2"), resolve(dist, "fonts/manrope-700.woff2")),
  cp(resolve(root, "node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2"), resolve(dist, "fonts/ibm-plex-mono-400.woff2")),
  cp(resolve(root, "node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-600-normal.woff2"), resolve(dist, "fonts/ibm-plex-mono-600.woff2")),
]);

await build({
  entryPoints: {
    background: resolve(root, "src/background.ts"),
    content: resolve(root, "src/content.ts"),
    popup: resolve(root, "src/popup.ts"),
  },
  outdir: dist,
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "chrome120",
  sourcemap: true,
  minify: false,
  logLevel: "info",
});
