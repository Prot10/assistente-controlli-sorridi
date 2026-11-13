"""WhatsApp Business Platform (Cloud API di Meta): webhook in arrivo, download delle foto, invio dei messaggi."""

import hashlib
import hmac
import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import certifi


ALTRI_TIPI = {"audio": "audio", "video": "video", "document": "documento", "sticker": "sticker"}
PERMESSI = {"whatsapp_business_messaging", "whatsapp_business_management"}
# Certificati del sistema più quelli di certifi: un Mac senza Homebrew non ne ha altri per il Python incluso nell'app.
TLS = ssl.create_default_context()
TLS.load_verify_locations(certifi.where())
CAMPI_NUMERO = "id,display_phone_number,verified_name,name_status,platform_type,status,code_verification_status,quality_rating"


ESTENSIONI = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class ErroreWhatsApp(Exception):
    pass


def firma_valida(segreto, corpo, intestazione):
    """Meta firma il corpo grezzo con l'app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not segreto or not intestazione or not intestazione.startswith("sha256="):
        return False
    atteso = hmac.new(segreto.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(atteso, intestazione[7:])


def eventi(payload):
    """Trasforma un webhook in ("messaggio", contatto, msg) e ("stato", wamid, stato, errore)."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            v = change.get("value", {})
            contatti = {}
            for c in v.get("contacts", []):
                cid = c.get("user_id") or c.get("wa_id")
                contatti[cid] = {"id": cid, "wa_id": c.get("wa_id"), "nome": c.get("profile", {}).get("name"),
                                 "username": c.get("profile", {}).get("username")}
                if c.get("wa_id"):
                    contatti.setdefault(c["wa_id"], contatti[cid])
            for m in v.get("messages", []):
                cid = m.get("from_user_id") or m.get("from")
                contatto = contatti.get(cid) or {"id": cid, "wa_id": m.get("from")}
                tipo = m.get("type")
                corpo = m.get(tipo, {}) if isinstance(m.get(tipo), dict) else {}
                testo = {"text": corpo.get("body"), "button": corpo.get("text")}.get(tipo, corpo.get("caption"))
                if tipo not in ("text", "image", "button"):
                    testo = f"[{ALTRI_TIPI.get(tipo, tipo)} non letto]"
                out.append(("messaggio", contatto, {
                    "id": m["id"], "tipo": tipo, "testo": testo or "",
                    "media_id": corpo.get("id") if tipo == "image" else None,
                    "quando": datetime.fromtimestamp(int(m.get("timestamp", 0))).isoformat(timespec="seconds")}))
            for s in v.get("statuses", []):
                errori = s.get("errors") or []
                out.append(("stato", s.get("id"), s.get("status"),
                            "; ".join(f"{e.get('code')}: {e.get('title') or e.get('message')}" for e in errori) or None))
    return out


class Cloud:
    def __init__(self, imp):
        self.token = imp["wa_token"]
        self.numero = imp["wa_phone_number_id"]
        self.base = f"{imp['wa_base'].rstrip('/')}/{imp['wa_versione']}"
        self.radice = imp["wa_base"].rstrip("/")

    def configurato(self):
        return bool(self.token and self.numero)

    def _chiama(self, url, dati=None, grezzo=False, token=None):
        req = urllib.request.Request(url, data=json.dumps(dati).encode() if dati is not None else None,
                                     headers={"Authorization": f"Bearer {token or self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60, context=TLS) as r:
                corpo = r.read()
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", {})
                raise ErroreWhatsApp(f"{err.get('code', e.code)}: {err.get('message', e.reason)}") from None
            except ValueError:
                raise ErroreWhatsApp(f"HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise ErroreWhatsApp(f"connessione non riuscita: {e.reason}") from None
        return corpo if grezzo else json.loads(corpo)

    # ------------------------------------------------------------ configurazione guidata
    def scopri(self, segreto, app_id="", waba_id=""):
        """Dal solo token: app, permessi, scadenza, account WhatsApp e numeri. Con l'app secret, anche il webhook dell'app.

        Gli account si leggono dai permessi del token. Con un utente di sistema amministratore Meta
        può lasciarli fuori (permessi senza `target_ids`): allora si prova a chiederli all'utente e
        all'azienda, e resta sempre l'ID scritto a mano (`waba_id`).
        """
        d = self._chiama(f"{self.base}/debug_token?input_token={quote(self.token)}")["data"]
        if not d.get("is_valid"):
            raise ErroreWhatsApp("il token non è valido o è scaduto")
        app_id = d.get("app_id") or app_id
        info = {"app": {"id": app_id, "nome": d.get("application")}, "tipo": d.get("type"),
                "scade": d.get("expires_at") or 0, "mancano": sorted(PERMESSI - set(d.get("scopes", []))), "wabas": []}
        wabas = {t for g in d.get("granular_scopes", []) if g.get("scope") in PERMESSI for t in g.get("target_ids", [])}
        info["dal_token"] = bool(wabas)
        if not wabas:
            wabas = self._wabas_raggiungibili()
        for w in sorted(wabas | ({waba_id} if waba_id else set())):
            try:
                iscritte = self._chiama(f"{self.base}/{w}/subscribed_apps").get("data", [])
                nome = self._chiama(f"{self.base}/{w}?fields=name").get("name")
                numeri = self._chiama(f"{self.base}/{w}/phone_numbers?fields={CAMPI_NUMERO}").get("data", [])
            except ErroreWhatsApp as e:
                info["wabas"].append({"id": w, "errore": str(e), "numeri": [], "altre_app": []})
                continue
            nostra = next((a for a in iscritte if a.get("whatsapp_business_api_data", {}).get("id") == app_id), None)
            altre = [a.get("whatsapp_business_api_data", {}) for a in iscritte if a is not nostra]
            info["wabas"].append({
                "id": w, "nome": nome, "numeri": numeri,
                "app_iscritta": nostra is not None, "indirizzo": (nostra or {}).get("override_callback_uri"),
                # un'altra app (per esempio un fornitore di invii) riceve già i messaggi di questo account
                "altre_app": [a.get("name") or a.get("id") or "un'altra app" for a in altre]})
        info["segreto_ok"] = info["webhook_app"] = None
        if segreto and app_id:
            # Il token dell'app (id|secret) funziona solo se il secret è giusto: è anche la prova del secret.
            try:
                abb = self._chiama(f"{self.base}/{app_id}/subscriptions", token=f"{app_id}|{segreto}").get("data", [])
                info["segreto_ok"] = True
                wa = next((a for a in abb if a.get("object") == "whatsapp_business_account"), None)
                info["webhook_app"] = wa and {"url": wa.get("callback_url"),
                                              "messages": any(f.get("name") == "messages" for f in wa.get("fields", []))}
            except ErroreWhatsApp as e:
                info["segreto_ok"], info["errore_segreto"] = False, str(e)
        return info

    def _wabas_raggiungibili(self):
        """Gli account WhatsApp assegnati all'utente del token o dell'azienda, se Meta lo permette."""
        trovati = set()
        try:
            trovati |= {w["id"] for w in self._chiama(f"{self.base}/me/assigned_whatsapp_business_accounts?fields=id").get("data", [])}
        except ErroreWhatsApp:
            pass
        try:  # serve il permesso business_management, che il token di solito non ha
            for b in self._chiama(f"{self.base}/me/businesses?fields=id").get("data", []):
                for lato in ("owned_whatsapp_business_accounts", "client_whatsapp_business_accounts"):
                    trovati |= {w["id"] for w in self._chiama(f"{self.base}/{b['id']}/{lato}?fields=id").get("data", [])}
        except ErroreWhatsApp:
            pass
        return trovati

    def registra(self, pin):
        """Attiva il numero sulla Cloud API. Il PIN è quello della verifica in due passaggi (se manca, lo imposta)."""
        return self._chiama(f"{self.base}/{self.numero}/register", {"messaging_product": "whatsapp", "pin": pin})

    def indirizza(self, waba_id, url, verifica):
        """Iscrive l'app all'account WhatsApp e manda i suoi webhook a questo indirizzo. Meta lo verifica subito."""
        return self._chiama(f"{self.base}/{waba_id}/subscribed_apps", {"override_callback_uri": url, "verify_token": verifica})

    def scarica(self, media_id, cartella):
        """Due passi: l'id dà un URL temporaneo, che si scarica con lo stesso token."""
        info = self._chiama(f"{self.base}/{media_id}")
        if not info.get("url", "").startswith(("https://", self.radice)):
            raise ErroreWhatsApp("URL della foto non valido")
        dati = self._chiama(info["url"], grezzo=True)
        # Su Windows mimetypes legge il registro e per image/jpeg può rispondere .jfif: estensioni fisse.
        est = ESTENSIONI.get(info.get("mime_type", "").split(";")[0].strip(), ".jpg")
        p = Path(cartella) / f"{hashlib.sha1(media_id.encode()).hexdigest()[:20]}{est}"
        p.write_bytes(dati)
        return p

    def invia(self, contatto, testo):
        dati = {"messaging_product": "whatsapp", "recipient_type": "individual", "type": "text",
                "text": {"preview_url": False, "body": testo}}
        if contatto.get("wa_id"):
            dati["to"] = contatto["wa_id"]
        else:
            dati["recipient"] = contatto["id"]  # solo l'id di WhatsApp per l'azienda (BSUID)
        return self._chiama(f"{self.base}/{self.numero}/messages", dati)["messages"][0]["id"]
