"""WhatsApp Business Platform (Cloud API di Meta): webhook in arrivo, download delle foto, invio dei messaggi."""

import hashlib
import hmac
import json
import mimetypes
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


ALTRI_TIPI = {"audio": "audio", "video": "video", "document": "documento", "sticker": "sticker"}


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

    def _chiama(self, url, dati=None, grezzo=False):
        req = urllib.request.Request(url, data=json.dumps(dati).encode() if dati is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
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

    def prova(self):
        return self._chiama(f"{self.base}/{self.numero}?fields=display_phone_number,verified_name")

    def scarica(self, media_id, cartella):
        """Due passi: l'id dà un URL temporaneo, che si scarica con lo stesso token."""
        info = self._chiama(f"{self.base}/{media_id}")
        if not info.get("url", "").startswith(("https://", self.radice)):
            raise ErroreWhatsApp("URL della foto non valido")
        dati = self._chiama(info["url"], grezzo=True)
        est = mimetypes.guess_extension(info.get("mime_type", "image/jpeg")) or ".jpg"
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
