"""Storico locale: contatti, messaggi, richieste con esito e decisione dell'operatore, impostazioni."""

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

ISTRUZIONI_INIZIALI = (Path(__file__).resolve().parent / "harness" / "istruzioni-sorridi.md").read_text(encoding="utf-8")

PREDEFINITE = {
    "sforzo": "medium", "operatore": "",
    "wa_token": "", "wa_app_secret": "", "wa_verify_token": "",
    "wa_app_id": "", "wa_waba_id": "", "wa_phone_number_id": "",
    "wa_versione": "v26.0", "wa_base": "https://graph.facebook.com",
    "porta_webhook": 8341, "tunnel": "automatico", "comando_tunnel": "", "indirizzo_pubblico": "",
    "auto_invio": False, "auto_decisioni": ["OK"], "auto_confidenza": 90,
    # Notifiche di sistema dall'app desktop: tutte, proposte, operatore, mai; il suono si può spegnere.
    "notifiche": "proposte", "notifiche_suono": True,
    # Silenzio dopo l'ultimo messaggio prima dell'analisi: i medici spesso scrivono e mandano le foto minuti dopo.
    "attesa_secondi": 120,
    # Scritte dallo staff nella pagina Istruzioni; si parte dalla bozza in harness/.
    "istruzioni_risposta": ISTRUZIONI_INIZIALI, "istruzioni_modifica": {},
}
SEGRETE = {"wa_token", "wa_app_secret"}

SCHEMA_DB = """
CREATE TABLE IF NOT EXISTS impostazioni (chiave TEXT PRIMARY KEY, valore TEXT);
CREATE TABLE IF NOT EXISTS contatti (
  id TEXT PRIMARY KEY, wa_id TEXT, nome TEXT, username TEXT, ultimo_in TEXT);
CREATE TABLE IF NOT EXISTS richieste (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contatto TEXT NOT NULL, creata TEXT NOT NULL,
  stato TEXT NOT NULL,              -- nuova, analisi, proposta, inviata, scartata, errore
  esito TEXT, modello TEXT, secondi REAL, analizzata TEXT, errore TEXT,
  risposta_proposta TEXT, risposta_inviata TEXT, modificata INTEGER,
  decisione_operatore TEXT, operatore TEXT, decisa TEXT, automatica INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS messaggi (
  id TEXT PRIMARY KEY, contatto TEXT NOT NULL, direzione TEXT NOT NULL, quando TEXT NOT NULL,
  tipo TEXT, testo TEXT, media_id TEXT, media_file TEXT, media_errore TEXT,
  stato TEXT, errore TEXT, richiesta INTEGER, operatore TEXT, ricevuto TEXT);
-- Ogni cosa fatta su una richiesta o una chat, dall'assistente o da un operatore: serve a rileggere i casi.
CREATE TABLE IF NOT EXISTS registro (
  id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT NOT NULL, richiesta INTEGER, contatto TEXT,
  evento TEXT NOT NULL, operatore TEXT, dati TEXT);
CREATE INDEX IF NOT EXISTS m_contatto ON messaggi(contatto, quando);
CREATE INDEX IF NOT EXISTS g_richiesta ON registro(richiesta, id);
CREATE INDEX IF NOT EXISTS r_stato ON richieste(stato, creata);
"""


def cartella_dati():
    if os.environ.get("ASSISTENTE_DATI"):
        p = Path(os.environ["ASSISTENTE_DATI"])
    elif os.name == "nt":
        p = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AssistenteSorridi"
    elif sys.platform == "darwin":
        p = Path.home() / "Library/Application Support/AssistenteSorridi"
    else:
        p = Path.home() / ".local/share/assistente-sorridi"
    p.mkdir(parents=True, exist_ok=True)
    return p


def adesso():
    return datetime.now().isoformat(timespec="seconds")


class Archivio:
    def __init__(self, percorso=None):
        self.percorso = percorso or cartella_dati() / "storico.sqlite"
        self.db = sqlite3.connect(self.percorso, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA_DB)
        self.lock = threading.RLock()
        # Colonne aggiunte dopo la prima versione: gli archivi già creati le ricevono qui.
        colonne = {r["name"] for r in self.q("PRAGMA table_info(contatti)")}
        for nome, tipo in (("alias", "TEXT"), ("archiviata", "INTEGER DEFAULT 0")):
            if nome not in colonne:
                self.x(f"ALTER TABLE contatti ADD COLUMN {nome} {tipo}")

    def q(self, sql, *args):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, *args):
        with self.lock, self.db:
            return self.db.execute(sql, args).lastrowid

    # impostazioni
    def impostazioni(self):
        out = dict(PREDEFINITE)
        for r in self.q("SELECT chiave, valore FROM impostazioni"):
            out[r["chiave"]] = json.loads(r["valore"])
        return out

    def salva_impostazioni(self, nuove):
        for k, v in nuove.items():
            if k in PREDEFINITE and not (k in SEGRETE and v == ""):
                self.x("INSERT OR REPLACE INTO impostazioni VALUES (?, ?)", k, json.dumps(v))

    # messaggi in arrivo
    def ricevi(self, contatto, msg):
        """Salva un messaggio in arrivo e lo aggancia alla richiesta aperta del contatto, o ne apre una.
        Sotto il lock: più foto mandate insieme arrivano in webhook paralleli e non devono aprire due richieste."""
        with self.lock:
            nuove = self.q("SELECT COUNT(*) n FROM richieste")[0]["n"]
            rid = self._ricevi(contatto, msg)
            return rid, rid is not None and self.q("SELECT COUNT(*) n FROM richieste")[0]["n"] > nuove

    def _ricevi(self, contatto, msg):
        if self.q("SELECT 1 FROM messaggi WHERE id = ?", msg["id"]):
            return None  # Meta può consegnare due volte lo stesso webhook
        self.x("INSERT INTO contatti (id, wa_id, nome, username, ultimo_in) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT(id) DO UPDATE SET wa_id = COALESCE(excluded.wa_id, wa_id), "
               "nome = COALESCE(excluded.nome, nome), username = COALESCE(excluded.username, username), "
               "ultimo_in = MAX(COALESCE(ultimo_in, ''), excluded.ultimo_in), archiviata = 0",  # come WhatsApp: chi riscrive torna tra le chat
               contatto["id"], contatto.get("wa_id"), contatto.get("nome"), contatto.get("username"), msg["quando"])
        aperta = self.q("SELECT id FROM richieste WHERE contatto = ? AND stato IN ('nuova', 'analisi', 'proposta', 'errore') "
                        "ORDER BY id DESC LIMIT 1", contatto["id"])
        if aperta:
            rid = aperta[0]["id"]
            self.x("UPDATE richieste SET stato = CASE stato WHEN 'analisi' THEN 'analisi' ELSE 'nuova' END WHERE id = ?", rid)
        else:
            rid = self.x("INSERT INTO richieste (contatto, creata, stato) VALUES (?, ?, 'nuova')", contatto["id"], msg["quando"])
        self.x("INSERT INTO messaggi (id, contatto, direzione, quando, tipo, testo, media_id, stato, richiesta, ricevuto) "
               "VALUES (?, ?, 'in', ?, ?, ?, ?, 'ricevuto', ?, ?)",
               msg["id"], contatto["id"], msg["quando"], msg["tipo"], msg.get("testo"), msg.get("media_id"), rid, adesso())
        return rid

    def registra(self, evento, richiesta=None, contatto=None, operatore=None, **dati):
        if richiesta is not None and contatto is None:
            r = self.q("SELECT contatto FROM richieste WHERE id = ?", richiesta)
            contatto = r[0]["contatto"] if r else None
        self.x("INSERT INTO registro (quando, richiesta, contatto, evento, operatore, dati) VALUES (?, ?, ?, ?, ?, ?)",
               adesso(), richiesta, contatto, evento, operatore, json.dumps(dati, ensure_ascii=False, default=str))

    def stato_invio(self, wamid, stato, errore=None):
        self.x("UPDATE messaggi SET stato = ?, errore = COALESCE(?, errore) WHERE id = ? AND direzione = 'out'",
               stato, errore, wamid)

    def inviato(self, contatto, wamid, testo, richiesta=None, operatore=""):
        ora = adesso()
        self.x("INSERT INTO messaggi (id, contatto, direzione, quando, tipo, testo, stato, richiesta, operatore, ricevuto) "
               "VALUES (?, ?, 'out', ?, 'text', ?, 'inviato', ?, ?, ?)", wamid, contatto, ora, testo, richiesta, operatore, ora)

    def precedenti(self, contatto, rid, ore=6, quanti=8):
        """Gli ultimi messaggi della chat fuori da questa richiesta, già gestiti: solo come contesto per il modello."""
        da = (datetime.now() - timedelta(hours=ore)).isoformat(timespec="seconds")
        righe = self.q("SELECT direzione, quando, testo, media_id FROM messaggi WHERE contatto = ? "
                       "AND COALESCE(richiesta, -1) != ? AND quando >= ? ORDER BY quando DESC LIMIT ?", contatto, rid, da, quanti)
        return righe[::-1]

    def cancella_chat(self, contatto):
        """Toglie da questo PC la chat, le sue richieste e le sue foto (non tocca WhatsApp). Restituisce i file da cancellare."""
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM richieste WHERE contatto = ? AND stato = 'analisi'", (contatto,)).fetchone():
                raise ValueError("c'è un'analisi in corso su questa chat: fermatela o aspettate che finisca")
            file = [r[0] for r in self.db.execute("SELECT media_file FROM messaggi WHERE contatto = ? AND media_file IS NOT NULL",
                                                  (contatto,))]
            for tabella, campo in (("messaggi", "contatto"), ("richieste", "contatto"), ("registro", "contatto"), ("contatti", "id")):
                self.db.execute(f"DELETE FROM {tabella} WHERE {campo} = ?", (contatto,))
        return file

    def messaggi_richiesta(self, rid):
        return self.q("SELECT * FROM messaggi WHERE richiesta = ? AND direzione = 'in' ORDER BY quando", rid)

    def richiesta(self, rid):
        r = self.q("SELECT r.*, COALESCE(c.alias, c.nome) AS nome, c.wa_id, c.username, c.ultimo_in FROM richieste r "
                   "JOIN contatti c ON c.id = r.contatto WHERE r.id = ?", rid)
        return r[0] if r else None
