// A single, self-hosted browser module for the explicitly opted-in Studio pilot.
import { build } from "esbuild";

await build({
  entryPoints: ["./pilot.js"],
  outfile: "../app/api/dispatcher_assets/ui-sdk-pilot.bundle.js",
  bundle: true,
  format: "esm",
  platform: "browser",
  target: ["es2022"],
  minify: true,
  legalComments: "none",
  sourcemap: false,
  logLevel: "info",
});
