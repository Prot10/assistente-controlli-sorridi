// Scarica da npm il Codex ufficiale per questo sistema, nella versione fissata in backend/motore.py, e lo mette in vendor/codex.
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const radice = path.resolve(import.meta.dirname, "..");
const versione = readFileSync(path.join(radice, "backend", "motore.py"), "utf8").match(/VERSIONE_CODEX = "([^"]+)"/)[1];
const piattaforme = {
  "darwin-arm64": "aarch64-apple-darwin",
  "darwin-x64": "x86_64-apple-darwin",
  "win32-x64": "x86_64-pc-windows-msvc",
  "win32-arm64": "aarch64-pc-windows-msvc",
};
const chiave = `${process.platform}-${process.arch}`;
const tripla = piattaforme[chiave];
if (!tripla) throw new Error(`Codex non disponibile per ${chiave}`);

const tmp = mkdtempSync(path.join(os.tmpdir(), "codex-"));
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const nome = execFileSync(npm, ["pack", `@openai/codex@${versione}-${chiave}`, "--pack-destination", tmp, "--silent"],
  { encoding: "utf8", shell: process.platform === "win32" }).trim().split(/\r?\n/).pop();
execFileSync("tar", ["-xzf", path.join(tmp, nome), "-C", tmp], { stdio: "inherit" });

const destinazione = path.join(radice, "vendor", "codex");
rmSync(destinazione, { recursive: true, force: true });
// Tutta la cartella bin: codex-code-mode-host serve, perché Codex chiama gli strumenti MCP (griglia, zoom) attraverso la code mode.
cpSync(path.join(tmp, "package", "vendor", tripla, "bin"), destinazione, { recursive: true });
rmSync(tmp, { recursive: true, force: true });
console.log(`Codex ${versione} per ${chiave} in ${destinazione}`);
