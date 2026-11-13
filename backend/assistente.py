#!/usr/bin/env python3
"""Assistente controlli di Sorridi.

Riceve i messaggi della linea WhatsApp Business, li fa valutare a un modello
OpenAI tramite Codex e l'abbonamento ChatGPT, propone la risposta e la invia
quando l'operatore la approva (o da sola, se l'invio automatico è attivo).
Tiene lo storico di tutto. Libreria standard più Pillow per gli strumenti sulle foto.

    python assistente.py            # pagina su http://127.0.0.1:8340
    python assistente.py strumenti-foto FOTO...   # il server degli strumenti (serve all'app impacchettata)
"""

import argparse
import base64
import csv
import io
import json
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import motore
from archivio import ISTRUZIONI_INIZIALI, Archivio, SEGRETE, adesso, cartella_dati
from tunnel import Tunnel
from whatsapp import TLS, Cloud, ErroreWhatsApp, eventi, firma_valida

QUI = Path(__file__).resolve().parent
ATTESA = 20          # secondi di silenzio prima di analizzare: foto e testo arrivano in più messaggi
FINESTRA = timedelta(hours=24)  # fuori da questa finestra Meta accetta solo messaggi modello
APERTE = "('nuova','analisi','proposta','errore')"
DEL_TUNNEL = ("tunnel", "comando_tunnel", "indirizzo_pubblico", "porta_webhook")


def raggiungibile(url, verifica):
    """Chiama il proprio webhook dall'indirizzo pubblico come farà Meta. Un tunnel nuovo impiega qualche secondo.
    È solo una diagnosi: una rete che blocca l'indirizzo da dentro non lo blocca per Meta."""
    prova = secrets.token_hex(4)
    q = urlencode({"hub.mode": "subscribe", "hub.verify_token": verifica, "hub.challenge": prova})
    for _ in range(10):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=10, context=TLS) as r:
                if r.read().decode() == prova:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return False


class Assistente:
    def __init__(self, archivio):
        self.a = archivio
        self.accesso = motore.Accesso()
        self.codex = None
        self.media = cartella_dati() / "media"
        self.media.mkdir(exist_ok=True)
        self.tunnel = Tunnel(lambda url: threading.Thread(target=self.collega, args=(url,), daemon=True).start())
        self.meta = None       # quello che il token permette di vedere su Meta
        self.ricezione = {}    # indirizzo pubblico del webhook e se Meta lo ha accettato
        self.lock_invio = threading.Lock()
        self.lock_collega = threading.Lock()
        if not archivio.impostazioni()["wa_verify_token"]:
            archivio.salva_impostazioni({"wa_verify_token": secrets.token_urlsafe(18)})

    def cloud(self):
        return Cloud(self.a.impostazioni())

    # ------------------------------------------------------------ worker
    def ciclo(self):
        while True:
            try:
                self.scarica_foto()
                self.analizza_pronte()
            except Exception as e:  # il worker non deve fermarsi per un errore singolo
                print("worker:", e)
            time.sleep(2)

    def scarica_foto(self):
        da_fare = self.a.q("SELECT id, media_id FROM messaggi WHERE media_id IS NOT NULL AND media_file IS NULL "
                           "AND media_errore IS NULL")
        if not da_fare or not self.cloud().configurato():
            return
        for m in da_fare:
            try:
                p = self.cloud().scarica(m["media_id"], self.media)
                self.a.x("UPDATE messaggi SET media_file = ? WHERE id = ?", p.name, m["id"])
            except ErroreWhatsApp as e:
                self.a.x("UPDATE messaggi SET media_errore = ? WHERE id = ?", str(e), m["id"])

    def analizza_pronte(self):
        limite = (datetime.now() - timedelta(seconds=ATTESA)).isoformat(timespec="seconds")
        for r in self.a.q("SELECT id FROM richieste WHERE stato = 'nuova' ORDER BY id"):
            msgs = self.a.messaggi_richiesta(r["id"])
            if not msgs or max(m["ricevuto"] for m in msgs) > limite:
                continue
            if any(m["media_id"] and not m["media_file"] and not m["media_errore"] for m in msgs):
                continue
            self.analizza(r["id"])

    def analizza(self, rid):
        imp = self.a.impostazioni()
        self.a.x("UPDATE richieste SET stato = 'analisi', errore = NULL WHERE id = ?", rid)
        msgs = self.a.messaggi_richiesta(rid)
        inizio = max(m["ricevuto"] for m in msgs)
        dati = [{"quando": datetime.fromisoformat(m["quando"]), "testo": m["testo"] or "",
                 "immagine": str(self.media / m["media_file"]) if m["media_file"] else None} for m in msgs]
        try:
            r = motore.analizza(dati, imp["sforzo"], istruzioni=imp["istruzioni_risposta"])
        except (motore.Rifiutato, subprocess.TimeoutExpired) as e:
            self.a.x("UPDATE richieste SET stato = 'errore', errore = ? WHERE id = ?", str(e)[:500], rid)
            return
        e = r["esito"]
        self.a.x("UPDATE richieste SET stato = 'proposta', esito = ?, modello = ?, secondi = ?, analizzata = ?, "
                 "risposta_proposta = ? WHERE id = ?", json.dumps(e, ensure_ascii=False), r["modello"], r["secondi"],
                 adesso(), e["risposta_suggerita"], rid)
        # Arrivati altri messaggi durante l'analisi: si rifà con tutto.
        if any(m["ricevuto"] > inizio for m in self.a.messaggi_richiesta(rid)):
            self.a.x("UPDATE richieste SET stato = 'nuova' WHERE id = ?", rid)
        elif self.puo_inviare_da_solo(e, imp):
            try:
                self.invia(rid, e["risposta_suggerita"], "invio automatico", automatica=True)
            except ErroreWhatsApp as err:
                self.a.x("UPDATE richieste SET errore = ? WHERE id = ?", f"invio automatico non riuscito: {err}", rid)

    @staticmethod
    def puo_inviare_da_solo(e, imp):
        return (imp["auto_invio"] and e["tipo"] == "foto_controllo" and e["decisione"] in imp["auto_decisioni"]
                and not e["serve_operatore"] and e["confidenza"] >= int(imp["auto_confidenza"]))

    # ------------------------------------------------------------ azioni
    def invia(self, rid, testo, operatore, decisione=None, automatica=False):
        with self.lock_invio:  # un doppio clic non deve mandare due messaggi
            self._invia(rid, testo, operatore, decisione, automatica)

    def _invia(self, rid, testo, operatore, decisione, automatica):
        r = self.a.richiesta(rid)
        if not r or r["stato"] not in ("proposta", "errore", "nuova"):
            raise ErroreWhatsApp("richiesta già chiusa o ancora in analisi")
        if not testo:
            raise ErroreWhatsApp("il messaggio è vuoto")
        wamid = self.cloud().invia({"id": r["contatto"], "wa_id": r["wa_id"]}, testo)
        self.a.inviato(r["contatto"], wamid, testo, rid, operatore)
        esito = json.loads(r["esito"]) if r["esito"] else {}
        self.a.x("UPDATE richieste SET stato = 'inviata', risposta_inviata = ?, modificata = ?, operatore = ?, "
                 "decisa = ?, decisione_operatore = ?, automatica = ? WHERE id = ?",
                 testo, int(testo.strip() != (r["risposta_proposta"] or "").strip()), operatore, adesso(),
                 decisione or esito.get("decisione"), int(automatica), rid)

    def scarta(self, rid, operatore, decisione=None):
        r = self.a.richiesta(rid)
        esito = json.loads(r["esito"]) if r and r["esito"] else {}
        self.a.x("UPDATE richieste SET stato = 'scartata', operatore = ?, decisa = ?, decisione_operatore = ? WHERE id = ?",
                 operatore, adesso(), decisione or esito.get("decisione"), rid)

    # ------------------------------------------------------------ viste
    def vista_richiesta(self, r):
        r["esito"] = json.loads(r["esito"]) if r["esito"] else None
        r["messaggi"] = [self.vista_messaggio(m) for m in self.a.messaggi_richiesta(r["id"])]
        r["fuori_finestra"] = bool(r["ultimo_in"]) and datetime.now() - datetime.fromisoformat(r["ultimo_in"]) > FINESTRA
        return r

    @staticmethod
    def vista_messaggio(m):
        return {"id": m["id"], "direzione": m["direzione"], "quando": m["quando"], "testo": m["testo"],
                "foto": bool(m["media_file"]), "foto_errore": m["media_errore"], "stato": m["stato"],
                "errore": m["errore"], "operatore": m["operatore"]}

    def statistiche(self):
        rr = self.a.q("SELECT stato, esito, modificata, automatica, decisione_operatore, creata, decisa FROM richieste")
        chiuse = [r for r in rr if r["stato"] in ("inviata", "scartata")]
        per_dec = {}
        for r in chiuse:
            e = json.loads(r["esito"]) if r["esito"] else {}
            if e.get("tipo") != "foto_controllo":
                continue
            d = per_dec.setdefault(e["decisione"], {"totale": 0, "confermate": 0, "inviate_senza_modifiche": 0})
            d["totale"] += 1
            d["confermate"] += r["decisione_operatore"] == e["decisione"]
            d["inviate_senza_modifiche"] += r["stato"] == "inviata" and not r["modificata"]
        tempi = sorted((datetime.fromisoformat(r["decisa"]) - datetime.fromisoformat(r["creata"])).total_seconds() / 60
                       for r in chiuse if r["decisa"])
        return {
            "richieste": len(rr),
            "da_approvare": sum(r["stato"] in ("nuova", "analisi", "proposta", "errore") for r in rr),
            "inviate_senza_modifiche": sum(r["stato"] == "inviata" and not r["modificata"] and not r["automatica"] for r in rr),
            "inviate_modificate": sum(r["stato"] == "inviata" and bool(r["modificata"]) for r in rr),
            "inviate_automatiche": sum(r["stato"] == "inviata" and bool(r["automatica"]) for r in rr),
            "scartate": sum(r["stato"] == "scartata" for r in rr),
            "per_decisione": per_dec,
            "minuti_mediani": round(tempi[len(tempi) // 2], 1) if tempi else None,
        }

    def esporta_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "contatto", "nome", "creata", "stato", "tipo", "decisione_modello", "grado", "confidenza",
                    "decisione_operatore", "modello", "risposta_proposta", "risposta_inviata", "modificata",
                    "automatica", "operatore", "decisa"])
        for r in self.a.q("SELECT r.*, c.nome FROM richieste r JOIN contatti c ON c.id = r.contatto ORDER BY r.id"):
            e = json.loads(r["esito"]) if r["esito"] else {}
            w.writerow([r["id"], r["contatto"], r["nome"], r["creata"], r["stato"], e.get("tipo"), e.get("decisione"),
                        e.get("grado"), e.get("confidenza"), r["decisione_operatore"], r["modello"],
                        r["risposta_proposta"], r["risposta_inviata"], r["modificata"], r["automatica"],
                        r["operatore"], r["decisa"]])
        return buf.getvalue().encode("utf-8-sig")

    # ------------------------------------------------------------ collegamento a Meta
    def avvia_tunnel(self):
        """Il tunnel serve solo quando c'è un token: prima nessuna porta è esposta."""
        imp = self.a.impostazioni()
        if imp["wa_token"]:
            self.tunnel.avvia(imp)
        else:
            self.tunnel.ferma()

    def scopri(self):
        imp = self.a.impostazioni()
        try:
            m = self.cloud().scopri(imp["wa_app_secret"], imp["wa_app_id"], imp["wa_waba_id"])
        except ErroreWhatsApp as e:
            self.meta = {"errore": str(e)}
            return self.meta
        scelta = {"wa_app_id": m["app"]["id"] or ""}
        # un numero di un account che un'altra app usa già (per esempio Brevo) non si sceglie mai da solo
        numeri = [(w["id"], n["id"]) for w in m["wabas"] if not w["altre_app"] for n in w["numeri"]]
        if len(numeri) == 1 and not imp["wa_phone_number_id"]:
            scelta.update(wa_waba_id=numeri[0][0], wa_phone_number_id=numeri[0][1])
        self.a.salva_impostazioni(scelta)
        self.meta = m
        return m

    def collega(self, url):
        """Prova l'indirizzo pubblico da internet, poi lo comunica a Meta per l'account WhatsApp scelto."""
        with self.lock_collega:
            imp = self.a.impostazioni()
            r = {"url": f"{url}/webhook" if url else None, "raggiungibile": None, "meta": None,
                 "errore": self.tunnel.errore, "quando": adesso()}
            self.ricezione = r
            if not url:
                return
            r["raggiungibile"] = raggiungibile(r["url"], imp["wa_verify_token"])
            # Solo con un numero scelto: un ID scritto a mano può essere un account di un'altra app (Brevo).
            if imp["wa_token"] and imp["wa_waba_id"] and imp["wa_phone_number_id"]:
                # Anche se la prova da qui non passa: è la verifica di Meta a contare.
                try:
                    self.cloud().indirizza(imp["wa_waba_id"], r["url"], imp["wa_verify_token"])
                    r["meta"] = True
                except ErroreWhatsApp as e:
                    r["meta"], r["errore"] = False, f"Meta non ha accettato l'indirizzo: {e}"

    def vista_whatsapp(self):
        imp = self.a.impostazioni()
        ultimo = self.a.q("SELECT MAX(ricevuto) n FROM messaggi WHERE direzione = 'in'")[0]["n"]
        return {"meta": self.meta, "ricezione": self.ricezione, "tunnel": imp["tunnel"], "ultimo_in": ultimo,
                "token": bool(imp["wa_token"]), "segreto": bool(imp["wa_app_secret"]),
                **{k: imp[k] for k in ("wa_waba_id", "wa_phone_number_id", "wa_verify_token")}}


# ---------------------------------------------------------------- pagina locale

TIPI_FOTO = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
TIPI_STATICI = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2", ".png": "image/png"}
STATICI = {f.name for f in (QUI / "statico").iterdir() if f.suffix in TIPI_STATICI}


def salva_esempio(grado, dati):
    """Una foto della guida, ridotta a 1400 px e salvata come JPEG nella cartella dati."""
    from PIL import Image, ImageOps
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(dati))).convert("RGB")
    except Exception:  # noqa: BLE001 - Pillow solleva errori diversi per ogni formato
        raise ValueError("Il file non è un'immagine JPEG o PNG") from None
    im.thumbnail((1400, 1400))
    motore.cartella_esempi().mkdir(parents=True, exist_ok=True)
    im.save(motore.cartella_esempi() / f"guida-grado-{grado}.jpg", quality=90)


def gestore_pagina(app, porta):
    host_ok = {f"127.0.0.1:{porta}", f"localhost:{porta}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _invia(self, corpo, tipo, codice=200, extra=()):
            self.send_response(codice)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, dati, codice=200):
            self._invia(json.dumps(dati, ensure_ascii=False).encode(), "application/json; charset=utf-8", codice)

        def do_GET(self):
            if self.headers.get("Host") not in host_ok:  # contro DNS rebinding
                return self.send_error(403)
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            # Gli id dei messaggi WhatsApp finiscono spesso con "=", che la pagina manda come %3D.
            parti = [unquote(x) for x in url.path.strip("/").split("/")]
            a = app.a
            if url.path == "/":
                return self._invia((QUI / "pagina.html").read_bytes(), "text/html; charset=utf-8", extra=[(
                    "Content-Security-Policy", "default-src 'self'; img-src 'self' data:; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; font-src 'self'")])
            if parti[0] == "statico" and len(parti) == 2 and parti[1] in STATICI:
                f = QUI / "statico" / parti[1]
                return self._invia(f.read_bytes(), TIPI_STATICI[f.suffix])
            if url.path == "/api/stato":
                if app.codex is None or q.get("aggiorna"):
                    app.codex = app.accesso.stato()
                imp = a.impostazioni()
                return self._json({"codex": app.codex, "whatsapp": app.cloud().configurato(),
                                   "ricezione": bool(app.ricezione.get("meta")),
                                   "porta_webhook": imp["porta_webhook"], "auto_invio": imp["auto_invio"],
                                   "esempi": motore.esempi_mancanti(),
                                   "da_approvare": a.q(f"SELECT COUNT(*) n FROM richieste WHERE stato IN {APERTE}")[0]["n"]})
            if url.path == "/api/codex/accesso":
                return self._json(app.accesso.avanzamento())
            if url.path == "/api/impostazioni":
                imp = a.impostazioni()
                for k in SEGRETE:
                    imp[k] = "••••" + imp[k][-4:] if imp[k] else ""
                return self._json({**imp, "modello": motore.MODELLO})
            if url.path == "/api/whatsapp":
                return self._json(app.vista_whatsapp())
            if url.path == "/api/esempi":
                return self._json({"mancanti": motore.esempi_mancanti()})
            if parti[:2] == ["api", "esempi"] and len(parti) == 3 and parti[2] in ("1", "2", "3", "4"):
                f = motore.cartella_esempi() / f"guida-grado-{parti[2]}.jpg"
                return self._invia(f.read_bytes(), "image/jpeg") if f.exists() else self.send_error(404)
            if url.path == "/api/richieste":
                filtro = {"da_approvare": APERTE, "inviate": "('inviata')", "scartate": "('scartata')"}.get(
                    q.get("stato"), "('nuova','analisi','proposta','errore','inviata','scartata')")
                righe = a.q(f"SELECT r.*, c.nome, c.wa_id, c.username, c.ultimo_in FROM richieste r "
                            f"JOIN contatti c ON c.id = r.contatto WHERE r.stato IN {filtro} ORDER BY r.id DESC LIMIT 200")
                return self._json([app.vista_richiesta(r) for r in righe])
            if url.path == "/api/contatti":
                return self._json(a.q("SELECT c.*, (SELECT MAX(quando) FROM messaggi m WHERE m.contatto = c.id) ultimo "
                                      "FROM contatti c ORDER BY ultimo DESC"))
            if parti[:2] == ["api", "contatti"] and len(parti) == 4 and parti[3] == "messaggi":
                return self._json([app.vista_messaggio(m) for m in
                                   a.q("SELECT * FROM messaggi WHERE contatto = ? ORDER BY quando", parti[2])])
            if parti[:2] == ["api", "foto"] and len(parti) == 3:
                m = a.q("SELECT media_file FROM messaggi WHERE id = ?", parti[2])
                if not m or not m[0]["media_file"]:
                    return self.send_error(404)
                p = app.media / Path(m[0]["media_file"]).name
                return self._invia(p.read_bytes(), TIPI_FOTO.get(p.suffix.lower(), "image/jpeg"),
                                   extra=[("Cache-Control", "private, max-age=86400")])
            if url.path == "/api/statistiche":
                return self._json(app.statistiche())
            if url.path == "/api/esporta.csv":
                return self._invia(app.esporta_csv(), "text/csv; charset=utf-8",
                                   extra=[("Content-Disposition", 'attachment; filename="storico-assistente.csv"')])
            self.send_error(404)

        def do_POST(self):
            # L'intestazione personalizzata impedisce a un altro sito aperto nel browser di fare richieste qui.
            if self.headers.get("Host") not in host_ok or self.headers.get("X-Assistente") != "1":
                return self.send_error(403)
            corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            parti = [unquote(x) for x in urlparse(self.path).path.strip("/").split("/")]
            a = app.a
            operatore = a.impostazioni()["operatore"] or "operatore"
            try:
                if parti == ["api", "codex", "accedi"]:
                    app.accesso.avvia(dispositivo=bool(corpo.get("dispositivo")))
                    app.codex = None
                    return self._json({"ok": True})
                if parti == ["api", "codex", "esci"]:
                    app.accesso.esci()
                    app.codex = None
                    return self._json({"ok": True})
                if parti == ["api", "istruzioni"]:
                    testo = ISTRUZIONI_INIZIALI if corpo.get("iniziali") else str(corpo.get("testo", ""))
                    if len(testo) > 8000:
                        raise ValueError("le istruzioni superano gli 8000 caratteri")
                    modifica = {"operatore": operatore, "quando": adesso(), "iniziali": testo == ISTRUZIONI_INIZIALI}
                    a.salva_impostazioni({"istruzioni_risposta": testo, "istruzioni_modifica": modifica})
                    return self._json({"testo": testo, "modifica": modifica})
                if parti == ["api", "impostazioni"]:
                    vecchia = a.impostazioni()
                    a.salva_impostazioni(corpo)
                    nuova = a.impostazioni()
                    if any(nuova[k] != vecchia[k] for k in DEL_TUNNEL):
                        app.avvia_tunnel()
                    return self._json({"ok": True, "riavvio": int(nuova["porta_webhook"]) != int(vecchia["porta_webhook"])})
                if parti[:2] == ["api", "esempi"] and len(parti) == 3 and parti[2] in ("1", "2", "3", "4"):
                    salva_esempio(int(parti[2]), base64.b64decode(corpo["dati"]))
                    return self._json({"ok": True})
                if parti == ["api", "whatsapp", "verifica"]:
                    nuove = {k: corpo[k].strip() for k in ("wa_token", "wa_app_secret") if corpo.get(k, "").strip()}
                    if nuove.get("wa_token") and nuove["wa_token"] != a.impostazioni()["wa_token"]:
                        nuove.update(wa_app_id="", wa_waba_id="", wa_phone_number_id="")  # un altro token, un'altra app
                    a.salva_impostazioni(nuove)
                    app.scopri()
                    if not app.tunnel.url or "wa_token" in nuove:
                        app.avvia_tunnel()
                    return self._json(app.vista_whatsapp())
                if parti == ["api", "whatsapp", "account"]:
                    waba = str(corpo.get("waba", "")).strip()
                    if not re.fullmatch(r"\d{6,20}", waba):
                        raise ValueError("l'ID dell'account WhatsApp è fatto solo di cifre")
                    if waba != a.impostazioni()["wa_waba_id"]:
                        a.salva_impostazioni({"wa_waba_id": waba, "wa_phone_number_id": ""})
                    app.scopri()
                    if app.tunnel.url and a.impostazioni()["wa_phone_number_id"]:  # numero scelto da solo
                        threading.Thread(target=app.collega, args=(app.tunnel.url,), daemon=True).start()
                    return self._json(app.vista_whatsapp())
                if parti == ["api", "whatsapp", "numero"]:
                    conto = next((w for w in (app.meta or {}).get("wabas", []) if w["id"] == corpo["waba"]), None)
                    if conto and conto["altre_app"]:
                        raise ValueError(f"questo numero è già usato da {', '.join(conto['altre_app'])}: sceglierne un altro")
                    a.salva_impostazioni({"wa_waba_id": corpo["waba"], "wa_phone_number_id": corpo["numero"]})
                    threading.Thread(target=app.collega, args=(app.tunnel.url,), daemon=True).start()
                    return self._json(app.vista_whatsapp())
                if parti == ["api", "whatsapp", "registra"]:
                    if not re.fullmatch(r"\d{6}", corpo.get("pin", "")):
                        raise ValueError("il PIN è di 6 cifre")
                    app.cloud().registra(corpo["pin"])
                    app.scopri()
                    return self._json(app.vista_whatsapp())
                if parti == ["api", "whatsapp", "ricollega"]:
                    app.scopri()
                    if app.tunnel.url:
                        threading.Thread(target=app.collega, args=(app.tunnel.url,), daemon=True).start()
                    else:
                        app.avvia_tunnel()
                    return self._json(app.vista_whatsapp())
                if parti[:2] == ["api", "richieste"] and len(parti) == 4:
                    rid, azione = int(parti[2]), parti[3]
                    if azione == "invia":
                        app.invia(rid, corpo["testo"].strip(), operatore, corpo.get("decisione"))
                    elif azione == "scarta":
                        app.scarta(rid, operatore, corpo.get("decisione"))
                    elif azione == "rianalizza":
                        threading.Thread(target=app.analizza, args=(rid,), daemon=True).start()
                    return self._json({"ok": True})
                if parti[:2] == ["api", "contatti"] and len(parti) == 4 and parti[3] == "invia":
                    c = a.q("SELECT * FROM contatti WHERE id = ?", parti[2])[0]
                    testo = corpo["testo"].strip()
                    a.inviato(c["id"], app.cloud().invia(c, testo), testo, None, operatore)
                    return self._json({"ok": True})
            except ErroreWhatsApp as e:
                return self._json({"errore": f"WhatsApp: {e}"}, 502)
            except (KeyError, IndexError, ValueError) as e:
                return self._json({"errore": f"richiesta non valida: {e}"}, 400)
            self.send_error(404)

    return H


# ---------------------------------------------------------------- webhook di Meta

def gestore_webhook(app):
    class W(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            token = app.a.impostazioni()["wa_verify_token"]
            if (url.path == "/webhook" and q.get("hub.mode") == "subscribe" and token
                    and q.get("hub.verify_token") == token):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                return self.wfile.write(q.get("hub.challenge", "").encode())
            self.send_error(403)

        def do_POST(self):
            if urlparse(self.path).path != "/webhook":
                return self.send_error(404)
            corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if not firma_valida(app.a.impostazioni()["wa_app_secret"], corpo, self.headers.get("X-Hub-Signature-256")):
                return self.send_error(401)
            for ev in eventi(json.loads(corpo)):
                if ev[0] == "messaggio":
                    app.a.ricevi(ev[1], ev[2])
                else:
                    app.a.stato_invio(ev[1], ev[2], ev[3])
            self.send_response(200)  # risposta subito: foto e analisi le fa il worker
            self.end_headers()

    return W


def main():
    if sys.argv[1:2] == ["strumenti-foto"]:
        import strumenti_foto
        sys.argv = sys.argv[1:]
        return strumenti_foto.main()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--porta", type=int, default=8340)
    ap.add_argument("--non-aprire", action="store_true", help="non aprire il browser all'avvio")
    a = ap.parse_args()
    app = Assistente(Archivio())
    porta_wh = int(app.a.impostazioni()["porta_webhook"])
    try:
        pagina = ThreadingHTTPServer(("127.0.0.1", a.porta), gestore_pagina(app, a.porta))
        webhook = ThreadingHTTPServer(("127.0.0.1", porta_wh), gestore_webhook(app))
    except OSError as e:
        raise SystemExit(f"Porta {a.porta} o {porta_wh} già in uso: probabilmente l'assistente è già aperto. ({e})")
    threading.Thread(target=webhook.serve_forever, daemon=True).start()
    threading.Thread(target=app.ciclo, daemon=True).start()
    if app.a.impostazioni()["wa_token"]:
        threading.Thread(target=app.scopri, daemon=True).start()
    app.avvia_tunnel()
    # L'app desktop aspetta questa riga per aprire la finestra.
    print(f"Assistente su http://127.0.0.1:{a.porta}  ·  webhook su 127.0.0.1:{porta_wh}/webhook  ·  dati in {cartella_dati()}", flush=True)
    if not a.non_aprire:
        webbrowser.open(f"http://127.0.0.1:{a.porta}")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # chiusura ordinata: il tunnel si ferma con l'assistente
    try:
        pagina.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.tunnel.ferma()


if __name__ == "__main__":
    main()
