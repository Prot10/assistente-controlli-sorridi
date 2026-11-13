#!/usr/bin/env python3
"""Meta finta per provare l'assistente senza un numero WhatsApp vero.

Fa la parte della Graph API (foto, invio, configurazione guidata) e manda all'assistente
webhook firmati come farebbe Meta, compresi gli stati di consegna.

    python prova/finto_meta.py serve --segreto PROVA
    python prova/finto_meta.py pannello --url https://.../webhook --verifica TOKEN   # il webhook messo a mano nel pannello Meta
    python prova/finto_meta.py manda --nome "Paziente Prova" --numero 390000000001 --testo "Ecco la foto" --foto foto.jpg

Nell'assistente: base API http://127.0.0.1:8399, app secret uguale a --segreto, token qualsiasi.
Il numero parte non registrato e l'app senza webhook nel pannello, come un account appena creato.
Con --come-sorridi il token è quello di un utente di sistema amministratore: i permessi non
elencano gli account (niente target_ids), e c'è un secondo account con un numero già usato da
Brevo, come nel portfolio di Sorridi il 13 novembre 2026.
Webhook messaggi: quello indicato a Meta (override, poi pannello), altrimenti --webhook.
"""

import argparse
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTA = 8399
FOTO = {}
INVIATI = []
APP, WABA, NUMERO = "APP-PROVA", "100000000000001", "NUMERO-PROVA"  # gli ID degli account sono numeri, come su Meta
BREVO, NUMERO_BREVO = "100000000000002", "NUMERO-BREVO"
CONTO = {"registrato": False, "pannello": None, "override": None}


def verifica_url(url, token):
    """Come Meta: GET con hub.challenge, valido solo se torna indietro uguale."""
    sfida = uuid.uuid4().hex[:10]
    q = urllib.parse.urlencode({"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": sfida})
    try:
        with urllib.request.urlopen(f"{url}?{q}", timeout=20) as r:
            return r.read().decode() == sfida
    except OSError:
        return False


def numero():
    return {"id": NUMERO, "display_phone_number": "+39 000 000 0000", "verified_name": "Sorridi (prova)",
            "name_status": "APPROVED", "status": "CONNECTED" if CONTO["registrato"] else "PENDING",
            "platform_type": "CLOUD_API" if CONTO["registrato"] else "NOT_APPLICABLE",
            "code_verification_status": "VERIFIED", "quality_rating": "GREEN"}


def destinazione(a):
    return CONTO["override"] or CONTO["pannello"] or a.webhook


def webhook(url, segreto, valore):
    corpo = json.dumps({"object": "whatsapp_business_account",
                        "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "390000000000", "phone_number_id": "NUMERO-PROVA"},
                            **valore}}]}]}).encode()
    firma = "sha256=" + hmac.new(segreto.encode(), corpo, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=corpo, headers={"Content-Type": "application/json", "X-Hub-Signature-256": firma})
    return urllib.request.urlopen(req, timeout=10).status


def serve(a):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *x):
            pass

        def _json(self, dati, codice=200):
            corpo = json.dumps(dati).encode()
            self.send_response(codice)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            p = self.path.split("?")[0].strip("/").split("/")
            token = self.headers.get("Authorization", "")[7:]
            if p[-1] == "debug_token":
                return self._json({"data": {"app_id": APP, "application": "Assistente Sorridi (prova)", "type": "SYSTEM_USER",
                                            "is_valid": True, "expires_at": 0,
                                            "scopes": ["whatsapp_business_messaging", "whatsapp_business_management"],
                                            "granular_scopes": [{"scope": s} if a.come_sorridi else {"scope": s, "target_ids": [WABA]}
                                                                for s in ("whatsapp_business_messaging", "whatsapp_business_management")]}})
            if p[1:] in (["me", "assigned_whatsapp_business_accounts"],):
                return self._json({"data": [] if a.come_sorridi else [{"id": WABA}]})
            if p[1:] == ["me", "businesses"]:
                return self._json({"error": {"code": 100, "message": "(#100) Missing Permission"}}, 400)
            if a.come_sorridi and p[1:] == [BREVO]:
                return self._json({"id": BREVO, "name": "Sorridi SRL"})
            if a.come_sorridi and p[1:] == [BREVO, "phone_numbers"]:
                return self._json({"data": [{"id": NUMERO_BREVO, "display_phone_number": "+39 000 000 0099", "verified_name": "Sorridi",
                                             "name_status": "APPROVED", "status": "CONNECTED", "platform_type": "CLOUD_API"}]})
            if a.come_sorridi and p[1:] == [BREVO, "subscribed_apps"]:
                return self._json({"data": [{"whatsapp_business_api_data": {"id": "APP-BREVO", "name": "Brevo"}}]})
            if p[1:] == [APP, "subscriptions"]:
                if token != f"{APP}|{a.segreto}":
                    return self._json({"error": {"code": 190, "message": "Invalid OAuth access token"}}, 400)
                return self._json({"data": [{"object": "whatsapp_business_account", "callback_url": CONTO["pannello"], "active": True,
                                             "fields": [{"name": "messages", "version": "v26.0"}]}] if CONTO["pannello"] else []})
            if p[1:] == [WABA]:
                return self._json({"id": WABA, "name": "Sorridi (prova)"})
            if p[1:] == [WABA, "phone_numbers"]:
                return self._json({"data": [numero()]})
            if p[1:] == [WABA, "subscribed_apps"]:
                voce = {"whatsapp_business_api_data": {"id": APP, "name": "Assistente Sorridi (prova)"}}
                if CONTO["override"]:
                    voce["override_callback_uri"] = CONTO["override"]
                return self._json({"data": [voce] if CONTO["override"] or CONTO["pannello"] else []})
            if p[0] == "scarica" and p[1] in FOTO:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                return self.wfile.write(FOTO[p[1]])
            if p[0] == "_destinazione":
                return self._json({"url": destinazione(a)})
            if p[0] == "inviati":
                return self._json(INVIATI)
            if len(p) == 2 and p[1] in FOTO:
                return self._json({"url": f"http://127.0.0.1:{PORTA}/scarica/{p[1]}", "mime_type": "image/jpeg", "id": p[1]})
            if p[1:] == [NUMERO]:
                return self._json(numero())
            self._json({"error": {"code": 100, "message": "non trovato"}}, 404)

        def do_POST(self):
            corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            p = self.path.strip("/").split("/")
            if p == ["_carica"]:
                mid = uuid.uuid4().hex[:16]
                FOTO[mid] = corpo
                return self._json({"id": mid})
            if p == ["_pannello"]:
                d = json.loads(corpo)
                if not verifica_url(d["url"], d["verifica"]):
                    return self._json({"error": {"code": 2200, "message": "Callback verification failed"}}, 400)
                CONTO["pannello"] = d["url"]
                return self._json({"success": True})
            if p[1:] == [NUMERO, "register"]:
                if json.loads(corpo).get("pin", "").isdigit():
                    CONTO["registrato"] = True
                    return self._json({"success": True})
                return self._json({"error": {"code": 100, "message": "pin non valido"}}, 400)
            if p[1:] == [WABA, "subscribed_apps"]:
                d = json.loads(corpo or b"{}")
                if d.get("override_callback_uri") and not verifica_url(d["override_callback_uri"], d.get("verify_token", "")):
                    return self._json({"error": {"code": 2200, "message": "Callback verification failed"}}, 400)
                CONTO["override"] = d.get("override_callback_uri")
                return self._json({"success": True})
            if len(p) == 3 and p[2] == "messages":
                if not CONTO["registrato"]:
                    return self._json({"error": {"code": 133010, "message": "Account not registered"}}, 400)
                msg = json.loads(corpo)
                wamid = "wamid.PROVA" + uuid.uuid4().hex[:12]
                INVIATI.append({"id": wamid, **msg})
                destinatario = msg.get("to") or msg.get("recipient")

                def stati():
                    for s in ("sent", "delivered", "read"):
                        time.sleep(1.5)
                        webhook(destinazione(a), a.segreto, {"statuses": [{"id": wamid, "status": s, "timestamp": str(int(time.time())),
                                                                      "recipient_id": destinatario}]})
                threading.Thread(target=stati, daemon=True).start()
                return self._json({"messaging_product": "whatsapp", "messages": [{"id": wamid}]})
            self._json({"error": {"code": 100, "message": "non trovato"}}, 404)

    print(f"Meta finta su http://127.0.0.1:{PORTA}")
    ThreadingHTTPServer(("127.0.0.1", PORTA), H).serve_forever()


def manda(a):
    messaggio = {"from": a.numero, "from_user_id": f"IT.{a.numero}", "id": "wamid.IN" + uuid.uuid4().hex[:12],
                 "timestamp": str(int(time.time()))}
    if a.foto:
        req = urllib.request.Request(f"http://127.0.0.1:{PORTA}/_carica", data=Path(a.foto).read_bytes())
        mid = json.loads(urllib.request.urlopen(req).read())["id"]
        messaggio.update(type="image", image={"id": mid, "mime_type": "image/jpeg", "caption": a.testo or ""})
    else:
        messaggio.update(type="text", text={"body": a.testo})
    contatto = {"profile": {"name": a.nome}, "wa_id": a.numero, "user_id": f"IT.{a.numero}"}
    dove = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORTA}/_destinazione").read())["url"]
    print(dove, webhook(dove, a.segreto, {"contacts": [contatto], "messages": [messaggio]}))


def pannello(a):
    req = urllib.request.Request(f"http://127.0.0.1:{PORTA}/_pannello", data=json.dumps({"url": a.url, "verifica": a.verifica}).encode())
    print(urllib.request.urlopen(req).read().decode())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=["serve", "manda", "pannello"])
    ap.add_argument("--segreto", default="PROVA")
    ap.add_argument("--webhook", default="http://127.0.0.1:8341/webhook")
    ap.add_argument("--nome", default="Paziente Prova")
    ap.add_argument("--numero", default="390000000001")
    ap.add_argument("--testo", default="")
    ap.add_argument("--foto")
    ap.add_argument("--url")
    ap.add_argument("--verifica")
    ap.add_argument("--come-sorridi", action="store_true", help="token senza account nei permessi e un account già usato da Brevo")
    a = ap.parse_args()
    {"serve": serve, "manda": manda, "pannello": pannello}[a.comando](a)


if __name__ == "__main__":
    main()
