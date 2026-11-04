"""Tunnel verso il webhook: rende raggiungibile da Meta solo la porta del webhook, non la pagina.

Automatico: cloudflared incluso nell'app apre un tunnel veloce di Cloudflare, senza account; l'indirizzo
cambia a ogni avvio e l'assistente lo comunica a Meta da solo. Personalizzato: un vostro comando
(per esempio un tunnel Cloudflare con nome o un dominio ngrok fisso) e il suo indirizzo pubblico.
"""

import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path

INDIRIZZO = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
SENZA_FINESTRA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def cloudflared():
    if os.environ.get("ASSISTENTE_CLOUDFLARED"):
        return os.environ["ASSISTENTE_CLOUDFLARED"]
    vendor = Path(__file__).resolve().parent.parent / "vendor" / "cloudflared" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    return str(vendor) if vendor.exists() else shutil.which("cloudflared")


class Tunnel:
    def __init__(self, al_cambio):
        self.al_cambio = al_cambio  # chiamata con l'indirizzo pubblico (o None) ogni volta che cambia
        self.proc = None
        self.url = None
        self.errore = None
        self.giro = 0

    def ferma(self):
        self.giro += 1
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    def avvia(self, imp):
        self.ferma()
        self.url, self.errore = None, None
        if imp["tunnel"] == "automatico":
            threading.Thread(target=self._automatico, args=(self.giro, int(imp["porta_webhook"])), daemon=True).start()
            return
        if imp["comando_tunnel"].strip():
            self.proc = subprocess.Popen(shlex.split(imp["comando_tunnel"], posix=os.name != "nt"), creationflags=SENZA_FINESTRA)
        self.url = imp["indirizzo_pubblico"].strip().rstrip("/") or None
        self.al_cambio(self.url)

    def _automatico(self, giro, porta):
        """Tiene vivo cloudflared: se si chiude, lo riapre e annuncia il nuovo indirizzo."""
        while giro == self.giro:
            exe = cloudflared()
            if not exe:
                self.errore = "cloudflared non trovato"
                return self.al_cambio(None)
            self.proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{porta}"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                         encoding="utf-8", errors="replace", creationflags=SENZA_FINESTRA)
            for riga in self.proc.stderr:
                m = INDIRIZZO.search(riga)
                if m and giro == self.giro and m.group(0) != self.url:
                    self.url, self.errore = m.group(0), None
                    self.al_cambio(self.url)
            if giro != self.giro:
                return
            self.url, self.errore = None, f"cloudflared si è chiuso (codice {self.proc.wait()}): lo riapro"
            self.al_cambio(None)
            time.sleep(5)
