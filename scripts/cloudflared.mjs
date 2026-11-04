// Scarica cloudflared (il tunnel che rende raggiungibile il webhook da Meta) per questo sistema e lo mette in vendor/cloudflared.
// Versione e impronte fissate qui: si aggiornano insieme, a mano.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, renameSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const VERSIONE = "2026.10.0";
const FILE = {
  "darwin-arm64": ["cloudflared-darwin-arm64.tgz", "a2f79ff7b9420aa537d74af239f376da170bbabeb529aec416002adac6a72e70"],
  "win32-x64": ["cloudflared-windows-amd64.exe", "86aee4017b26625cee8484c113558f48effa4cd47f7aa05fcf425604e5d2b23c"],
};
const chiave = `${process.platform}-${process.arch}`;
if (!FILE[chiave]) throw new Error(`cloudflared non previsto per ${chiave}`);
const [nome, impronta] = FILE[chiave];

const r = await fetch(`https://github.com/cloudflare/cloudflared/releases/download/${VERSIONE}/${nome}`);
if (!r.ok) throw new Error(`download di ${nome}: HTTP ${r.status}`);
const dati = Buffer.from(await r.arrayBuffer());
const calcolata = createHash("sha256").update(dati).digest("hex");
if (calcolata !== impronta) throw new Error(`${nome}: impronta ${calcolata}, attesa ${impronta}`);

const radice = path.resolve(import.meta.dirname, "..");
const destinazione = path.join(radice, "vendor", "cloudflared");
rmSync(destinazione, { recursive: true, force: true });
mkdirSync(destinazione, { recursive: true });
if (nome.endsWith(".exe")) {
  writeFileSync(path.join(destinazione, "cloudflared.exe"), dati);
} else {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "cloudflared-"));
  writeFileSync(path.join(tmp, nome), dati);
  execFileSync("tar", ["-xzf", path.join(tmp, nome), "-C", tmp]);
  renameSync(path.join(tmp, "cloudflared"), path.join(destinazione, "cloudflared"));
  rmSync(tmp, { recursive: true, force: true });
}
console.log(`cloudflared ${VERSIONE} per ${chiave} in ${destinazione}`);
