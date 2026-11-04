#!/usr/bin/env python3
"""Meta finta per provare l'assistente senza un numero WhatsApp vero.

Fa la parte della Graph API (foto, invio, prova del token) e manda all'assistente
webhook firmati come farebbe Meta, compresi gli stati di consegna.

    python prova/finto_meta.py serve --segreto PROVA
    python prova/finto_meta.py manda --nome "Paziente Prova" --numero 390000000001 --testo "Ecco la foto" --foto foto.jpg

Nell'assistente: base API http://127.0.0.1:8399, app secret uguale a --segreto, token qualsiasi.
"""

import argparse
import hashlib
import hmac
import json
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTA = 8399
FOTO = {}
INVIATI = []


def webhook(url, segreto, valore):
    corpo = json.dumps({"object": "whatsapp_business_account",
                        "entry": [{"id": "WABA-PROVA", "changes": [{"field": "messages", "value": {
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
            if p[0] == "scarica" and p[1] in FOTO:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                return self.wfile.write(FOTO[p[1]])
            if p[0] == "inviati":
                return self._json(INVIATI)
            if len(p) == 2 and p[1] in FOTO:
                return self._json({"url": f"http://127.0.0.1:{PORTA}/scarica/{p[1]}", "mime_type": "image/jpeg", "id": p[1]})
            if len(p) == 2:
                return self._json({"display_phone_number": "+39 000 000 0000", "verified_name": "Sorridi (prova)", "id": p[1]})
            self._json({"error": {"code": 100, "message": "non trovato"}}, 404)

        def do_POST(self):
            corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            p = self.path.strip("/").split("/")
            if p == ["_carica"]:
                mid = uuid.uuid4().hex[:16]
                FOTO[mid] = corpo
                return self._json({"id": mid})
            if len(p) == 3 and p[2] == "messages":
                msg = json.loads(corpo)
                wamid = "wamid.PROVA" + uuid.uuid4().hex[:12]
                INVIATI.append({"id": wamid, **msg})
                destinatario = msg.get("to") or msg.get("recipient")

                def stati():
                    for s in ("sent", "delivered", "read"):
                        time.sleep(1.5)
                        webhook(a.webhook, a.segreto, {"statuses": [{"id": wamid, "status": s, "timestamp": str(int(time.time())),
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
    print(webhook(a.webhook, a.segreto, {"contacts": [contatto], "messages": [messaggio]}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=["serve", "manda"])
    ap.add_argument("--segreto", default="PROVA")
    ap.add_argument("--webhook", default="http://127.0.0.1:8341/webhook")
    ap.add_argument("--nome", default="Paziente Prova")
    ap.add_argument("--numero", default="390000000001")
    ap.add_argument("--testo", default="")
    ap.add_argument("--foto")
    a = ap.parse_args()
    serve(a) if a.comando == "serve" else manda(a)


if __name__ == "__main__":
    main()
