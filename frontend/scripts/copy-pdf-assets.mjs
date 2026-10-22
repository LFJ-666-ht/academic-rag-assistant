import { cpSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const source = dirname(
  fileURLToPath(import.meta.resolve("pdfjs-dist/package.json")),
);
const dest = fileURLToPath(new URL("../public/pdf-assets/", import.meta.url));
mkdirSync(dest, { recursive: true });
for (const folder of ["cmaps", "standard_fonts", "wasm"])
  cpSync(join(source, folder), join(dest, folder), { recursive: true });
console.log("PDF preview assets prepared locally.");
