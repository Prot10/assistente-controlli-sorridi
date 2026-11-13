// Guscio desktop: avvia il backend Python, apre la sua pagina in una finestra e tiene l'app aggiornata.
const { app, BrowserWindow, Notification, dialog, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { spawn, execFile } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");

const PORTA = 8340;
const PRONTO = /Assistente su (http:\/\/127\.0\.0\.1:\d+)/;
const OGNI_ORE = 4;

let finestra = null;
let backend = null;
let indirizzo = null;
let registro = null;
let uscendo = false;
let cursore = -1;            // ultimo evento già visto da /api/notifiche
const aperte = new Set();    // le notifiche restano referenziate, altrimenti il clic si perde

// Su Windows una notifica compare solo se l'app ha il suo AppUserModelId, lo stesso del collegamento dell'installer.
app.setAppUserModelId("it.allineatorisorridi.assistente-controlli");

function scrivi(riga) {
  registro.write(`${new Date().toISOString()} ${riga}\n`);
}

function portaLibera(porta) {
  // La porta abituale se è libera, così la pagina tiene le sue preferenze; altrimenti una qualsiasi.
  return new Promise((ok) => {
    const s = net.createServer();
    s.once("error", () => (porta ? portaLibera(0).then(ok) : ok(0)));
    s.listen(porta, "127.0.0.1", () => {
      const p = s.address().port;
      s.close(() => ok(p));
    });
  });
}

function comandoBackend(porta) {
  const args = ["--porta", String(porta), "--non-aprire"];
  if (!app.isPackaged) {
    // Sviluppo: Python di sistema, Codex da npx e cloudflared da vendor/, come da riga di comando.
    return { cmd: process.platform === "win32" ? "python" : "python3",
             args: [path.join(__dirname, "..", "backend", "assistente.py"), ...args], env: {} };
  }
  const exe = process.platform === "win32" ? ".exe" : "";
  const risorse = process.resourcesPath;
  return { cmd: path.join(risorse, "backend", `assistente${exe}`), args,
           env: { ASSISTENTE_CODEX: path.join(risorse, "codex", `codex${exe}`),
                  ASSISTENTE_CLOUDFLARED: path.join(risorse, "cloudflared", `cloudflared${exe}`) } };
}

async function avviaBackend() {
  const porta = await portaLibera(PORTA);
  const { cmd, args, env } = comandoBackend(porta);
  scrivi(`avvio ${cmd} ${args.join(" ")}`);
  // Su Mac il backend guida un suo gruppo di processi (Codex, tunnel): alla chiusura li fermo tutti insieme.
  backend = spawn(cmd, args, { env: { ...process.env, ...env, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1" },
                               detached: process.platform !== "win32", windowsHide: true });
  return new Promise((ok, ko) => {
    let coda = "";
    const leggi = (dati) => {
      const testo = dati.toString();
      registro.write(testo);
      coda = (coda + testo).slice(-4000);
      const m = !indirizzo && coda.match(PRONTO);
      if (m) {
        indirizzo = m[1];
        ok(indirizzo);
      }
    };
    backend.stdout.on("data", leggi);
    backend.stderr.on("data", leggi);
    backend.on("error", (e) => ko(new Error(`${e.message}\n\n${coda}`)));
    backend.on("exit", (codice) => {
      scrivi(`backend uscito con ${codice}`);
      backend = null;
      if (!indirizzo) ko(new Error(`Il servizio si è fermato all'avvio (codice ${codice}).\n\n${coda.slice(-1500)}`));
      else if (!uscendo) {
        dialog.showErrorBox("Assistente controlli", `Il servizio si è fermato (codice ${codice}). Riapri l'app.\n\nRegistro: ${registro.path}`);
        app.quit();
      }
    });
  });
}

function fermaBackend() {
  if (!backend) return;
  const pid = backend.pid;
  if (process.platform === "win32") execFile("taskkill", ["/pid", String(pid), "/T", "/F"], () => {});
  else {
    try { process.kill(-pid, "SIGTERM"); } catch { /* già fermo */ }
  }
}

function apriFinestra() {
  finestra = new BrowserWindow({
    width: 1320, height: 880, minWidth: 900, minHeight: 600, title: "Assistente controlli",
    backgroundColor: "#f6f8fa", show: false,
    // Senza rallentamenti in secondo piano: la pagina continua ad aggiornare le richieste anche ridotta a icona.
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false },
  });
  finestra.once("ready-to-show", () => finestra.show());
  // Solo la pagina del backend resta nella finestra; ogni altro indirizzo si apre nel browser.
  const esterno = (url) => !url.startsWith(indirizzo);
  finestra.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  finestra.webContents.on("will-navigate", (ev, url) => {
    if (esterno(url)) {
      ev.preventDefault();
      if (/^https?:/.test(url)) shell.openExternal(url);
    }
  });
  finestra.on("closed", () => (finestra = null));
  finestra.loadURL(indirizzo);
}

// Notifiche di sistema: il backend dice cosa è successo e cosa mostrare secondo le impostazioni della pagina.
function notifiche() {
  const giro = async () => {
    try {
      const r = await fetch(`${indirizzo}/api/notifiche?dopo=${cursore}`, { headers: { "X-Assistente": "1" } });
      const d = await r.json();
      cursore = d.ultimo;
      for (const e of d.eventi) mostra(e, d.suono);
    } catch { /* il backend si sta riavviando: si riprova al giro dopo */ }
  };
  giro();
  setInterval(giro, 4000);
}

function mostra(e, suono) {
  scrivi(`notifica ${e.tipo} ${e.rid ?? ""}: ${e.titolo}${suono ? "" : " (senza suono)"}`);
  if (!Notification.isSupported()) return scrivi("notifiche non supportate su questo sistema");
  const n = new Notification({ title: e.titolo, body: e.testo, silent: !suono, sound: suono ? "Glass" : undefined,
    urgency: e.operatore ? "critical" : "normal", timeoutType: e.operatore ? "never" : "default" });
  aperte.add(n);
  const via = () => aperte.delete(n);
  n.on("click", () => { via(); apriRichiesta(e.rid); });
  n.on("close", via);
  n.show();
}

function apriRichiesta(rid) {
  if (!finestra) apriFinestra();
  if (finestra.isMinimized()) finestra.restore();
  finestra.show();
  finestra.focus();
  const id = Number(rid) || 0;
  finestra.webContents.executeJavaScript(`location.hash = "#richieste"; setTimeout(() => document.getElementById("r${id}")?.scrollIntoView({block: "center", behavior: "smooth"}), 700);`).catch(() => {});
}

function aggiornamenti() {
  if (!app.isPackaged) return;
  autoUpdater.logger = { info: scrivi, warn: scrivi, error: scrivi, debug: () => {} };
  autoUpdater.on("update-downloaded", async (info) => {
    const { response } = await dialog.showMessageBox(finestra, {
      type: "info", buttons: ["Riavvia ora", "Più tardi"], defaultId: 0, cancelId: 1,
      message: `È pronta la versione ${info.version}.`,
      detail: "Riavvia per installarla. Se scegli più tardi, si installa alla prossima chiusura dell'app.",
    });
    if (response === 0) {
      uscendo = true;
      fermaBackend();
      autoUpdater.quitAndInstall();
    }
  });
  const controlla = () => autoUpdater.checkForUpdates().catch((e) => scrivi(`aggiornamenti: ${e.message}`));
  controlla();
  setInterval(controlla, OGNI_ORE * 3600 * 1000);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (finestra) {
      if (finestra.isMinimized()) finestra.restore();
      finestra.focus();
    }
  });

  app.whenReady().then(async () => {
    fs.mkdirSync(app.getPath("logs"), { recursive: true });
    registro = fs.createWriteStream(path.join(app.getPath("logs"), "assistente.log"), { flags: "a" });
    scrivi(`avvio versione ${app.getVersion()}`);
    try {
      await avviaBackend();
    } catch (e) {
      scrivi(e.message);
      dialog.showErrorBox("Assistente controlli", `${e.message}\n\nRegistro: ${registro.path}`);
      app.quit();
      return;
    }
    apriFinestra();
    notifiche();
    aggiornamenti();
    app.on("activate", () => {
      if (!finestra) apriFinestra();
    });
  });

  // Su Mac chiudere la finestra non chiude l'app: il webhook deve continuare a ricevere i messaggi.
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
  app.on("before-quit", () => {
    uscendo = true;
    fermaBackend();
  });
}
