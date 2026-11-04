// Congela il backend Python con PyInstaller in vendor/backend, per il sistema su cui gira.
import { execFileSync } from "node:child_process";
import { rmSync, renameSync } from "node:fs";
import path from "node:path";

const radice = path.resolve(import.meta.dirname, "..");
const sorgenti = path.join(radice, "backend");
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const dati = (nome, dest) => ["--add-data", `${path.join(sorgenti, nome)}${path.delimiter}${dest}`];

rmSync(path.join(radice, "vendor", "backend"), { recursive: true, force: true });
execFileSync(python, [
  "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console", "--name", "assistente",
  "--distpath", path.join(radice, "vendor"), "--workpath", path.join(radice, "build-py"),
  "--specpath", path.join(radice, "build-py"), "--paths", sorgenti,
  "--hidden-import", "strumenti_foto", "--exclude-module", "tkinter",
  ...dati("harness", "harness"), ...dati("statico", "statico"), ...dati("pagina.html", "."),
  path.join(sorgenti, "assistente.py"),
], { stdio: "inherit" });
renameSync(path.join(radice, "vendor", "assistente"), path.join(radice, "vendor", "backend"));
