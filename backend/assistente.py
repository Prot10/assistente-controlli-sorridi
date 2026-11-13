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
import difflib
import io
import json
import re
import secrets
import signal
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import motore
from archivio import ISTRUZIONI_INIZIALI, Archivio, SEGRETE, adesso, cartella_dati

# Nello zip dei dati non vanno mai le chiavi di Meta né il token del webhook.
RISERVATE = SEGRETE | {"wa_verify_token"}


def differenze(prima, dopo):
    """Come l'operatore ha cambiato la risposta proposta, parola per parola."""
    a, b = (prima or "").split(), (dopo or "").split()
    nomi = {"replace": "cambiato", "delete": "tolto", "insert": "aggiunto"}
    return [{"cosa": nomi[op], "prima": " ".join(a[i1:i2]), "dopo": " ".join(b[j1:j2])}
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal"]


def somiglianza(prima, dopo):
    return round(difflib.SequenceMatcher(None, prima or "", dopo or "", autojunk=False).ratio(), 3)
from tunnel import Tunnel
from whatsapp import TLS, Cloud, ErroreWhatsApp, eventi, firma_valida

QUI = Path(__file__).resolve().parent
LEGGIMI_ZIP = """Dati dell'Assistente controlli di Sorridi, scaricati il {quando}.

casi/       una cartella per richiesta: caso.json (messaggi ricevuti, valutazione del modello,
            risposta proposta e inviata, come è stata cambiata, decisione del modello e
            dell'operatore, registro della richiesta) e le foto numerate come nell'app.
richieste.csv   una riga per richiesta.
messaggi.csv    tutti i messaggi, ricevuti e inviati, con la foto che corrisponde.
registro.csv    ogni azione in ordine di tempo: analisi, invii, scarti, decisioni corrette, chat.
impostazioni.json   le impostazioni dell'app, senza le chiavi di Meta.

Contiene foto e messaggi dei pazienti: mandatelo solo per canali riservati e cancellatelo quando non serve più.
"""
# Attese possibili prima dell'analisi (impostazione attesa_secondi): foto e testo arrivano in più messaggi.
ATTESE = (20, 60, 120, 300, 600, 900)
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
        self.subito = set()    # richieste da analizzare senza aspettare (pulsante "Analizza ora")
        self.lavoro = {}       # richiesta in analisi -> {inizio, passi}: cosa sta facendo l'agente
        self.eventi, self.n_eventi = [], 0  # per le notifiche dell'app desktop (/api/notifiche)
        self.lock_eventi = threading.Lock()
        self.lock_collega = threading.Lock()
        # Un'analisi interrotta dalla chiusura dell'app (o da un aggiornamento) resterebbe "in analisi" per sempre: riparte.
        for r in archivio.q("SELECT id FROM richieste WHERE stato = 'analisi'"):
            archivio.registra("analisi_ripresa", r["id"], motivo="l'app è stata chiusa durante l'analisi")
        archivio.x("UPDATE richieste SET stato = 'nuova' WHERE stato = 'analisi'")
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
        limite = (datetime.now() - timedelta(seconds=self.attesa())).isoformat(timespec="seconds")
        for r in self.a.q("SELECT id FROM richieste WHERE stato = 'nuova' ORDER BY id"):
            msgs = self.a.messaggi_richiesta(r["id"])
            if not msgs or (max(m["ricevuto"] for m in msgs) > limite and r["id"] not in self.subito):
                continue
            if any(m["media_id"] and not m["media_file"] and not m["media_errore"] for m in msgs):
                continue
            self.subito.discard(r["id"])
            self.analizza(r["id"])

    def avvisa(self, tipo, rid, titolo, testo, operatore):
        """Un evento per le notifiche: l'app desktop li legge da /api/notifiche e li mostra secondo le impostazioni."""
        with self.lock_eventi:
            self.n_eventi += 1
            self.eventi = (self.eventi + [{"n": self.n_eventi, "tipo": tipo, "rid": rid, "titolo": titolo[:80],
                                           "testo": " ".join(testo.split())[:140], "operatore": operatore}])[-100:]

    def notifiche(self, dopo):
        imp = self.a.impostazioni()
        quali = {"tutte": {"messaggio", "proposta"}, "proposte": {"proposta"}, "operatore": {"operatore"}}.get(imp["notifiche"], set())
        with self.lock_eventi:
            nuovi = [] if dopo < 0 else [e for e in self.eventi if e["n"] > dopo]
            ultimo = self.n_eventi
        # La prova si mostra sempre: serve proprio a vedere com'è la notifica.
        mostra = [e for e in nuovi if e["tipo"] == "prova" or e["tipo"] in quali or ("operatore" in quali and e["operatore"])]
        return {"ultimo": ultimo, "suono": bool(imp["notifiche_suono"]), "eventi": mostra}

    @staticmethod
    def nota_fermata(operatore):
        return f"Analisi fermata da {operatore}: scrivete la risposta a mano oppure scartate la richiesta."

    def ferma(self, rid, operatore):
        """L'operatore sa già che l'analisi non serve: la ferma, o evita che parta. La richiesta resta aperta."""
        chi = operatore or "un operatore"
        self.subito.discard(rid)
        lavoro = self.lavoro.get(rid)
        if lavoro:
            lavoro["fermato"] = chi
            if lavoro.get("proc"):
                motore.chiudi(lavoro["proc"])
        elif self.a.q("SELECT 1 FROM richieste WHERE id = ? AND stato IN ('nuova', 'analisi')", rid):
            self.a.x("UPDATE richieste SET stato = 'proposta', errore = ? WHERE id = ?", self.nota_fermata(chi), rid)
            self.a.registra("analisi_saltata", rid, operatore=chi)

    def attesa(self):
        s = int(self.a.impostazioni()["attesa_secondi"])
        return s if s in ATTESE else 120

    def dato(self, m, gia_gestito=False):
        d = {"quando": datetime.fromisoformat(m["quando"]), "testo": m["testo"] or "",
             "immagine": str(self.media / m["media_file"]) if m["media_file"] else None}
        if gia_gestito:
            d["gia_gestito"] = True
        return d

    def analizza(self, rid):
        imp = self.a.impostazioni()
        self.a.x("UPDATE richieste SET stato = 'analisi', errore = NULL WHERE id = ?", rid)
        lavoro = self.lavoro[rid] = {"inizio": adesso(), "passi": []}

        def avanza(frase):
            if not lavoro["passi"] or lavoro["passi"][-1]["testo"] != frase:
                lavoro["passi"].append({"ora": adesso(), "testo": frase})
        try:
            if not self.smista(rid, lavoro, avanza):
                return  # i messaggi sono andati tutti in altre richieste
            msgs = self.a.messaggi_richiesta(rid)
            inizio = max(m["ricevuto"] for m in msgs)
            r0 = self.a.richiesta(rid)
            # Seguito di una richiesta già gestita: il modello rivede anche i messaggi e le foto di allora.
            prima = self.a.messaggi_richiesta(r0["segue"]) if r0["segue"] else []
            dati = [self.dato(m, True) for m in prima] + [self.dato(m) for m in msgs]
            contesto = [{"quando": datetime.fromisoformat(m["quando"]), "da": "Sorridi" if m["direzione"] == "out" else "mittente",
                         "testo": m["testo"] or "", "foto": bool(m["media_id"])}
                        for m in self.a.precedenti(r0["contatto"], rid, segue=r0["segue"])]
            self.a.registra("analisi_avviata", rid, messaggi=[m["id"] for m in msgs], foto=sum(1 for d in dati if d["immagine"]),
                            sforzo=imp["sforzo"], contesto=len(contesto), segue=r0["segue"])
            r = motore.analizza(dati, imp["sforzo"], istruzioni=imp["istruzioni_risposta"], contesto=contesto,
                                avanzamento=avanza, controllo=lavoro)
        except (motore.Rifiutato, subprocess.TimeoutExpired) as e:
            if lavoro.get("fermato"):
                self.a.x("UPDATE richieste SET stato = 'proposta', errore = ? WHERE id = ?", self.nota_fermata(lavoro["fermato"]), rid)
                self.a.registra("analisi_fermata", rid, operatore=lavoro["fermato"], passi=lavoro["passi"])
            else:
                self.a.x("UPDATE richieste SET stato = 'errore', errore = ? WHERE id = ?", str(e)[:500], rid)
                self.a.registra("analisi_errore", rid, errore=str(e)[:500], passi=lavoro["passi"])
            return
        finally:
            self.lavoro.pop(rid, None)
        e = r["esito"]
        self.a.x("UPDATE richieste SET stato = 'proposta', esito = ?, modello = ?, secondi = ?, analizzata = ?, "
                 "risposta_proposta = ? WHERE id = ?", json.dumps(e, ensure_ascii=False), r["modello"], r["secondi"],
                 adesso(), e["risposta_suggerita"], rid)
        self.a.registra("analisi_conclusa", rid, esito=e, modello=r["modello"], secondi=r["secondi"], passi=lavoro["passi"])
        # Arrivati altri messaggi durante l'analisi: si rifà con tutto.
        if any(m["ricevuto"] > inizio for m in self.a.messaggi_richiesta(rid)):
            self.a.x("UPDATE richieste SET stato = 'nuova' WHERE id = ?", rid)
        else:
            self.avvisa_proposta(rid, e, imp)
        if self.a.richiesta(rid)["stato"] == "proposta" and self.puo_inviare_da_solo(e, imp):
            try:
                self.invia(rid, e["risposta_suggerita"], "invio automatico", automatica=True)
            except ErroreWhatsApp as err:
                self.a.x("UPDATE richieste SET errore = ? WHERE id = ?", f"invio automatico non riuscito: {err}", rid)

    STATI_RECENTI = {"nuova": "in attesa", "analisi": "in analisi", "proposta": "risposta da inviare",
                     "errore": "risposta da inviare", "inviata": "risposta inviata", "scartata": "chiusa senza risposta"}

    def smista(self, rid, lavoro, avanza):
        """Divide i messaggi della richiesta per caso (un medico può mandare due pazienti di fila) e sposta quelli che
        continuano un'altra richiesta. Restituisce False se nella richiesta non resta nulla da analizzare."""
        msgs, r0 = self.a.messaggi_richiesta(rid), self.a.richiesta(rid)
        if not msgs or r0["smistati"] == len(msgs):
            return bool(msgs)
        da = (datetime.now() - timedelta(hours=12)).isoformat(timespec="seconds")
        recenti = []
        for x in self.a.q("SELECT * FROM richieste WHERE contatto = ? AND id != ? AND (stato IN ('nuova', 'proposta', 'errore') "
                          "OR creata >= ?) ORDER BY id DESC LIMIT 6", r0["contatto"], rid, da)[::-1]:
            dentro = self.a.messaggi_richiesta(x["id"])
            recenti.append({"id": x["id"], "stato": self.STATI_RECENTI.get(x["stato"], x["stato"]),
                            "ora": datetime.fromisoformat(x["creata"]).strftime("%d/%m %H:%M"), "paziente": x["paziente"],
                            "messaggi": [(m["testo"] or "") + (" [foto]" if m["media_id"] else "") for m in dentro][-10:],
                            "risposta": (x["risposta_inviata"] or x["risposta_proposta"] or "")[:400] or None})
        if len(msgs) < 2 and not recenti:
            self.a.x("UPDATE richieste SET smistati = ? WHERE id = ?", len(msgs), rid)
            return True
        avanza("Divide i messaggi per caso")
        try:
            esito = motore.smista([self.dato(m) for m in msgs], recenti, controllo=lavoro)
        except (motore.Rifiutato, subprocess.TimeoutExpired) as e:
            if lavoro.get("fermato"):
                raise
            # Senza smistamento si analizza tutto insieme, come prima.
            self.a.registra("smistamento_non_riuscito", rid, errore=str(e)[:300])
            self.a.x("UPDATE richieste SET smistati = ? WHERE id = ?", len(msgs), rid)
            return True
        aperte = {x["id"] for x in self.a.q("SELECT id FROM richieste WHERE contatto = ? AND id != ? "
                                            "AND stato IN ('nuova', 'proposta', 'errore')", r0["contatto"], rid)}
        resta = next((c for c in esito["casi"] if c["collega"] not in aperte), None)
        fatti = []
        for c in esito["casi"]:
            ids = [msgs[i]["id"] for i in c["messaggi"]]
            if c is resta:
                dove, come = rid, "resta"
                self.a.x("UPDATE richieste SET segue = ?, paziente = COALESCE(?, paziente) WHERE id = ?", c["collega"], c["paziente"], rid)
            elif c["collega"] in aperte:  # completa una richiesta ancora da inviare: si rianalizza con questi messaggi
                dove, come = c["collega"], "unita"
                self.sposta(ids, dove)
                self.a.x("UPDATE richieste SET stato = 'nuova', smistati = ?, paziente = COALESCE(paziente, ?) WHERE id = ?",
                         len(self.a.messaggi_richiesta(dove)), c["paziente"], dove)
                self.subito.add(dove)
            else:  # un altro caso: una richiesta sua, con la sua risposta
                dove, come = self.a.x("INSERT INTO richieste (contatto, creata, stato, segue, paziente, smistati) "
                                      "VALUES (?, ?, 'nuova', ?, ?, ?)", r0["contatto"], msgs[c["messaggi"][0]]["quando"],
                                      c["collega"], c["paziente"], len(ids)), "nuova"
                self.sposta(ids, dove)
                self.subito.add(dove)
            fatti.append({"richiesta": dove, "come": come, "messaggi": ids, "collega": c["collega"],
                          "paziente": c["paziente"], "motivo": c["motivo"]})
        self.a.registra("smistamento", rid, casi=fatti, secondi=esito["secondi"], modello=esito["modello"])
        rimasti = len(self.a.messaggi_richiesta(rid))
        if not rimasti:
            self.a.x("DELETE FROM richieste WHERE id = ?", rid)
            return False
        self.a.x("UPDATE richieste SET smistati = ? WHERE id = ?", rimasti, rid)
        return True

    def sposta(self, ids, rid):
        for i in ids:
            self.a.x("UPDATE messaggi SET richiesta = ? WHERE id = ?", rid, i)

    def avvisa_proposta(self, rid, e, imp):
        if self.puo_inviare_da_solo(e, imp):
            return  # parte da sola: nessuno deve intervenire
        r = self.a.richiesta(rid)
        chi = r["nome"] or r["wa_id"] or "WhatsApp"
        if e["serve_operatore"]:
            self.avvisa("proposta", rid, f"Serve un operatore · {chi}", e["motivo"], True)
        else:
            cosa = {"OK": "foto OK", "NON_OK": "foto non OK", "INCERTO": "foto da rivedere"}.get(e["decisione"], "risposta pronta")
            self.avvisa("proposta", rid, f"Proposta pronta · {chi}", f"{cosa[0].upper()}{cosa[1:]}: {e['risposta_suggerita']}", False)

    @staticmethod
    def puo_inviare_da_solo(e, imp):
        return (imp["auto_invio"] and e["tipo"] == "foto_controllo" and e["decisione"] in imp["auto_decisioni"]
                and not e["serve_operatore"] and e["confidenza"] >= int(imp["auto_confidenza"]))

    # ------------------------------------------------------------ azioni
    def invia(self, rid, testo, operatore, decisione=None, automatica=False):
        with self.lock_invio:  # un doppio clic non deve mandare due messaggi
            try:
                self._invia(rid, testo, operatore, decisione, automatica)
            except ErroreWhatsApp as e:
                self.a.registra("invio_fallito", rid, operatore=operatore, errore=str(e), testo=testo, automatica=automatica)
                raise

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
        proposta = r["risposta_proposta"] or ""
        self.a.registra("inviata", rid, operatore=operatore, automatica=automatica, wamid=wamid,
                        risposta_proposta=proposta or None, risposta_inviata=testo,
                        modificata=testo.strip() != proposta.strip(), differenze=differenze(proposta, testo) if proposta else None,
                        somiglianza=somiglianza(proposta, testo) if proposta else None,
                        **self.decisioni(esito, decisione), minuti_dalla_richiesta=self.minuti(r))

    def scarta(self, rid, operatore, decisione=None):
        r = self.a.richiesta(rid)
        esito = json.loads(r["esito"]) if r and r["esito"] else {}
        self.a.x("UPDATE richieste SET stato = 'scartata', operatore = ?, decisa = ?, decisione_operatore = ? WHERE id = ?",
                 operatore, adesso(), decisione or esito.get("decisione"), rid)
        if r:
            self.a.registra("scartata", rid, operatore=operatore, risposta_proposta=r["risposta_proposta"],
                            **self.decisioni(esito, decisione), minuti_dalla_richiesta=self.minuti(r))

    @staticmethod
    def decisioni(esito, decisione):
        """La decisione del modello e quella dell'operatore, per vedere dove l'operatore l'ha corretta."""
        modello = esito.get("decisione") if esito.get("tipo") == "foto_controllo" else None
        finale = decisione or modello
        return {"tipo": esito.get("tipo"), "decisione_modello": modello, "grado_modello": esito.get("grado"),
                "confidenza": esito.get("confidenza"), "serve_operatore": esito.get("serve_operatore"),
                "decisione_operatore": finale if modello else None,
                "decisione_corretta": bool(modello and finale and finale != modello)}

    @staticmethod
    def minuti(r):
        return round((datetime.now() - datetime.fromisoformat(r["creata"])).total_seconds() / 60, 1) if r.get("creata") else None

    # ------------------------------------------------------------ viste
    def vista_richiesta(self, r):
        r["esito"] = json.loads(r["esito"]) if r["esito"] else None
        r["messaggi"] = [self.vista_messaggio(m) for m in self.a.messaggi_richiesta(r["id"])]
        r["fuori_finestra"] = bool(r["ultimo_in"]) and datetime.now() - datetime.fromisoformat(r["ultimo_in"]) > FINESTRA
        lavoro = self.lavoro.get(r["id"]) if r["stato"] == "analisi" else None
        r["analisi_tra"], r["lavoro"] = None, lavoro and {"inizio": lavoro["inizio"], "passi": lavoro["passi"]}
        dentro = self.a.messaggi_richiesta(r["id"])
        r["seguito"] = None
        if r.get("segue"):
            p = self.a.q("SELECT id, stato, creata, decisa, risposta_inviata, paziente FROM richieste WHERE id = ?", r["segue"])
            if p:
                r["seguito"] = {**p[0], "messaggi": [self.vista_messaggio(m) for m in self.a.messaggi_richiesta(r["segue"])]}
        r["foto_attese"] = sum(1 for m in dentro if m["media_id"])
        r["foto_scaricate"] = sum(1 for m in dentro if m["media_file"] or m["media_errore"])
        if r["stato"] == "nuova" and dentro:
            ultimo = max(datetime.fromisoformat(m["ricevuto"]) for m in dentro)
            r["analisi_tra"] = 0 if r["id"] in self.subito else max(0, round(self.attesa() - (datetime.now() - ultimo).total_seconds()))
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
        for r in self.a.q("SELECT r.*, COALESCE(c.alias, c.nome) AS nome FROM richieste r JOIN contatti c ON c.id = r.contatto ORDER BY r.id"):
            e = json.loads(r["esito"]) if r["esito"] else {}
            w.writerow([r["id"], r["contatto"], r["nome"], r["creata"], r["stato"], e.get("tipo"), e.get("decisione"),
                        e.get("grado"), e.get("confidenza"), r["decisione_operatore"], r["modello"],
                        r["risposta_proposta"], r["risposta_inviata"], r["modificata"], r["automatica"],
                        r["operatore"], r["decisa"]])
        return buf.getvalue().encode("utf-8-sig")

    def esporta_zip(self, dest):
        a, foto_di = self.a, {}
        nomi = {c["id"]: c for c in a.q("SELECT * FROM contatti")}

        def nome(cid):
            c = nomi.get(cid) or {}
            return c.get("alias") or c.get("nome") or c.get("wa_id") or cid

        def csv_di(intestazione, righe):
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow(intestazione)
            w.writerows(righe)
            return buf.getvalue().encode("utf-8-sig")

        registro = a.q("SELECT * FROM registro ORDER BY id")
        for g in registro:
            g["dati"] = json.loads(g["dati"]) if g["dati"] else {}
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for r in a.q("SELECT * FROM richieste ORDER BY id"):
                cartella = f"casi/{r['id']:05d}-{re.sub(r'[^A-Za-z0-9]+', '-', str(nome(r['contatto']))).strip('-')[:40] or 'chat'}"
                ricevuti, k = [], 0
                for m in a.messaggi_richiesta(r["id"]):
                    f = None
                    if m["media_file"] and (self.media / Path(m["media_file"]).name).exists():
                        k += 1
                        p = self.media / Path(m["media_file"]).name
                        f = f"foto-{k}{p.suffix.lower()}"
                        z.write(p, f"{cartella}/{f}", compress_type=zipfile.ZIP_STORED)  # già compresse
                        foto_di[m["id"]] = f"{cartella}/{f}"
                    ricevuti.append({"id": m["id"], "quando": m["quando"], "tipo": m["tipo"], "testo": m["testo"],
                                     "foto": f, "foto_non_scaricata": m["media_errore"]})
                inviati = a.q("SELECT id, quando, testo, stato, errore, operatore FROM messaggi "
                              "WHERE richiesta = ? AND direzione = 'out' ORDER BY quando", r["id"])
                esito = json.loads(r["esito"]) if r["esito"] else None
                c = nomi.get(r["contatto"]) or {}
                caso = {
                    "richiesta": r["id"], "chat": {"nome": c.get("nome"), "rinominata": c.get("alias"), "numero": c.get("wa_id")},
                    "creata": r["creata"], "stato": r["stato"], "paziente": r["paziente"], "segue_la_richiesta": r["segue"],
                    "messaggi_ricevuti": ricevuti,
                    "valutazione": esito, "modello": r["modello"], "secondi_analisi": r["secondi"],
                    "risposta_proposta": r["risposta_proposta"], "risposta_inviata": r["risposta_inviata"],
                    "testo_modificato": bool(r["modificata"]) if r["stato"] == "inviata" else None,
                    "differenze": differenze(r["risposta_proposta"], r["risposta_inviata"])
                    if r["risposta_proposta"] and r["risposta_inviata"] else None,
                    **self.decisioni(esito or {}, r["decisione_operatore"]),
                    "operatore": r["operatore"], "decisa": r["decisa"], "automatica": bool(r["automatica"]),
                    "messaggi_inviati": inviati, "errore": r["errore"],
                    "registro": [{k2: g[k2] for k2 in ("quando", "evento", "operatore", "dati")} for g in registro if g["richiesta"] == r["id"]],
                }
                z.writestr(f"{cartella}/caso.json", json.dumps(caso, ensure_ascii=False, indent=2))
            z.writestr("richieste.csv", self.esporta_csv())
            z.writestr("messaggi.csv", csv_di(
                ["id", "chat", "nome", "richiesta", "direzione", "quando", "tipo", "testo", "foto", "foto_non_scaricata",
                 "stato", "errore", "operatore"],
                [[m["id"], m["contatto"], nome(m["contatto"]), m["richiesta"], m["direzione"], m["quando"], m["tipo"], m["testo"],
                  foto_di.get(m["id"]), m["media_errore"], m["stato"], m["errore"], m["operatore"]]
                 for m in a.q("SELECT * FROM messaggi ORDER BY quando")]))
            z.writestr("registro.csv", csv_di(
                ["id", "quando", "evento", "richiesta", "chat", "nome", "operatore", "dettagli"],
                [[g["id"], g["quando"], g["evento"], g["richiesta"], g["contatto"], nome(g["contatto"]) if g["contatto"] else "",
                  g["operatore"], json.dumps(g["dati"], ensure_ascii=False)] for g in registro]))
            z.writestr("impostazioni.json", json.dumps(
                {k: v for k, v in a.impostazioni().items() if k not in RISERVATE}, ensure_ascii=False, indent=2))
            z.writestr("LEGGIMI.txt", LEGGIMI_ZIP.format(quando=adesso()))

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
                righe = a.q(f"SELECT r.*, COALESCE(c.alias, c.nome) AS nome, c.wa_id, c.username, c.ultimo_in FROM richieste r "
                            f"JOIN contatti c ON c.id = r.contatto WHERE r.stato IN {filtro} ORDER BY r.id DESC LIMIT 200")
                return self._json([app.vista_richiesta(r) for r in righe])
            if url.path == "/api/contatti":
                archiviate = int(q.get("archiviate", 0))
                return self._json({
                    "contatti": a.q("SELECT c.*, (SELECT MAX(quando) FROM messaggi m WHERE m.contatto = c.id) ultimo "
                                    "FROM contatti c WHERE COALESCE(c.archiviata, 0) = ? ORDER BY ultimo DESC", archiviate),
                    "archiviate": a.q("SELECT COUNT(*) n FROM contatti WHERE archiviata = 1")[0]["n"]})
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
            if url.path == "/api/notifiche":
                return self._json(app.notifiche(int(q.get("dopo", -1))))
            if url.path == "/api/statistiche":
                return self._json(app.statistiche())
            if url.path == "/api/esporta.zip":
                fd, tmp = tempfile.mkstemp(suffix=".zip")
                try:
                    with open(fd, "wb") as f:
                        app.esporta_zip(f)
                    nome = f"dati-assistente-{datetime.now():%Y-%m-%d-%H%M}.zip"
                    self.send_response(200)
                    for k, v in (("Content-Type", "application/zip"), ("Cache-Control", "no-store"),
                                 ("Content-Length", str(Path(tmp).stat().st_size)),
                                 ("Content-Disposition", f'attachment; filename="{nome}"')):
                        self.send_header(k, v)
                    self.end_headers()
                    with open(tmp, "rb") as f:
                        while blocco := f.read(1 << 20):
                            self.wfile.write(blocco)
                finally:
                    Path(tmp).unlink(missing_ok=True)
                return
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
                if parti == ["api", "notifiche", "prova"]:
                    app.avvisa("prova", None, "Assistente controlli", "Così compaiono gli avvisi: cliccate qui per aprire le richieste.", False)
                    return self._json({"ok": True})
                if parti == ["api", "impostazioni"]:
                    if corpo.get("notifiche", "proposte") not in ("tutte", "proposte", "operatore", "mai"):
                        raise ValueError("scelta delle notifiche non valida")
                    if "attesa_secondi" in corpo and int(corpo["attesa_secondi"]) not in ATTESE:
                        raise ValueError("attesa non valida")
                    vecchia = a.impostazioni()
                    a.salva_impostazioni(corpo)
                    nuova = a.impostazioni()
                    cambiate = {k: nuova[k] for k in nuova if nuova[k] != vecchia.get(k) and k not in RISERVATE}
                    if cambiate:
                        a.registra("impostazioni", operatore=operatore, cambiate=cambiate)
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
                    elif azione == "analizza":
                        app.subito.add(rid)
                        a.registra("analizza_ora", rid, operatore=operatore)
                    elif azione == "ferma":
                        app.ferma(rid, operatore)
                    elif azione == "rianalizza":
                        a.registra("rianalizza", rid, operatore=operatore)
                        threading.Thread(target=app.analizza, args=(rid,), daemon=True).start()
                    return self._json({"ok": True})
                if parti[:2] == ["api", "contatti"] and len(parti) == 4 and parti[3] in ("rinomina", "archivia", "cancella"):
                    cid = parti[2]
                    if parti[3] == "rinomina":
                        nome = " ".join(str(corpo.get("nome", "")).split())[:80]
                        prima = a.q("SELECT alias, nome FROM contatti WHERE id = ?", cid)
                        a.x("UPDATE contatti SET alias = ? WHERE id = ?", nome or None, cid)
                        a.registra("chat_rinominata", contatto=cid, operatore=operatore, nome=nome or None,
                                   prima=prima[0]["alias"] if prima else None)
                    elif parti[3] == "archivia":
                        archivia = bool(corpo.get("archiviata", True))
                        a.x("UPDATE contatti SET archiviata = ? WHERE id = ?", int(archivia), cid)
                        a.registra("chat_archiviata" if archivia else "chat_ripristinata", contatto=cid, operatore=operatore)
                    else:
                        quanti = a.q("SELECT COUNT(*) n FROM messaggi WHERE contatto = ?", cid)[0]["n"]
                        file = a.cancella_chat(cid)
                        for f in file:
                            (app.media / Path(f).name).unlink(missing_ok=True)
                        a.registra("chat_cancellata", contatto=cid, operatore=operatore, messaggi=quanti, foto=len(file))
                    return self._json({"ok": True})
                if parti[:2] == ["api", "contatti"] and len(parti) == 4 and parti[3] == "invia":
                    c = a.q("SELECT * FROM contatti WHERE id = ?", parti[2])[0]
                    testo = corpo["testo"].strip()
                    wamid = app.cloud().invia(c, testo)
                    a.inviato(c["id"], wamid, testo, None, operatore)
                    a.registra("messaggio_dalla_chat", contatto=c["id"], operatore=operatore, testo=testo, wamid=wamid)
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
                    rid, nuova = app.a.ricevi(ev[1], ev[2])
                    if nuova:
                        app.a.registra("richiesta_aperta", rid, primo_messaggio=ev[2]["id"])
                        c = app.a.q("SELECT COALESCE(alias, nome, wa_id) n FROM contatti WHERE id = ?", ev[1]["id"])
                        app.avvisa("messaggio", rid, f"Nuovo messaggio · {c[0]['n'] if c and c[0]['n'] else 'WhatsApp'}",
                                   ev[2].get("testo") or "Foto", False)
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
