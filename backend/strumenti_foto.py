"""Strumenti sulle foto di controllo per l'agente, come server MCP su stdio (senza dipendenze oltre Pillow).

Sono gli stessi della skill aligner-photo-inspect: griglia e zoom in coordinate percentuali, con contrasto
e griglia nel ritaglio. Il server conosce solo le foto passate all'avvio e risponde con l'immagine prodotta:
il modello non ha comandi, file o rete.

  python strumenti_foto.py FOTO1 [FOTO2 ...] [--massimo 4]
"""

import argparse
import base64
import io
import json
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageOps

LATO = 1400
SOTTILE = 200  # sotto questi pixel reali uno zoom mostra la compressione, non dettagli

FOTO = {"type": "string", "description": "Quale foto: \"immagine 1\", \"immagine 2\", ... nell'ordine dei messaggi."}
PERCENTO = {"type": "number", "minimum": 0, "maximum": 100}
STRUMENTI = [
    {"name": "griglia",
     "description": "La foto intera con una linea ogni 10% e i numeri ai bordi, per leggere le coordinate dello zoom.",
     "inputSchema": {"type": "object", "properties": {"foto": FOTO}, "required": ["foto"], "additionalProperties": False}},
    {"name": "zoom",
     "description": "Ingrandisce la zona da (x1, y1) a (x2, y2), in percentuale della foto (0-100), e dice quanti pixel reali "
                    "contiene. griglia: linee ogni 10% del ritaglio, per misurare la banda rispetto alla corona. "
                    "contrasto: solo per distinguere una banda da un riflesso o dalla saliva; esagera i bordi, quindi non misurare con il contrasto.",
     "inputSchema": {"type": "object", "properties": {
         "foto": FOTO, "x1": PERCENTO, "y1": PERCENTO, "x2": PERCENTO, "y2": PERCENTO,
         "griglia": {"type": "boolean"}, "contrasto": {"type": "boolean"}},
         "required": ["foto", "x1", "y1", "x2", "y2"], "additionalProperties": False}},
]


def disegna_griglia(img, numeri=True):
    d = ImageDraw.Draw(img)
    w, h = img.size
    for p in range(10, 100, 10):
        x, y = w * p // 100, h * p // 100
        d.line((x, 0, x, h), fill=(255, 230, 0), width=1)
        d.line((0, y, w, y), fill=(255, 230, 0), width=1)
        if numeri:
            d.rectangle((x + 2, 2, x + 22, 16), fill="black")
            d.text((x + 4, 3), str(p), fill="white")
            d.rectangle((2, y + 2, 22, y + 16), fill="black")
            d.text((4, y + 3), str(p), fill="white")
    return img


def adatta(img):
    s = LATO / max(img.size)
    return img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS), s


def griglia(img, _):
    out, _ = adatta(img)
    return disegna_griglia(out), f"foto intera {img.width}x{img.height} px"


def zoom(img, a):
    w, h = img.size
    x1, x2 = sorted((a["x1"], a["x2"]))
    y1, y2 = sorted((a["y1"], a["y2"]))
    ritaglio = img.crop((round(w * x1 / 100), round(h * y1 / 100), round(w * x2 / 100), round(h * y2 / 100)))
    if not ritaglio.width or not ritaglio.height:
        raise ValueError("zona vuota")
    out, s = adatta(ritaglio)
    if a.get("contrasto"):
        out = ImageOps.autocontrast(out, cutoff=1).filter(ImageFilter.UnsharpMask(radius=3, percent=80))
    if a.get("griglia"):
        disegna_griglia(out, numeri=False)
    poco = min(ritaglio.size) < SOTTILE / 2 or max(ritaglio.size) < SOTTILE
    return out, f"reali {ritaglio.width}x{ritaglio.height} px, ingrandito x{s:.1f}" + (" (poco dettaglio)" if poco else "")


class Server:
    def __init__(self, foto, massimo):
        self.foto, self.massimo, self.fatte = foto, massimo, 0

    def chiama(self, nome, a):
        if nome not in ("griglia", "zoom"):
            raise ValueError(f"strumento sconosciuto: {nome}")
        if self.fatte >= self.massimo:
            raise ValueError(f"limite di {self.massimo} operazioni raggiunto: decidi con quello che hai")
        try:
            n = int(str(a.get("foto", "")).split()[-1])
            percorso = self.foto[n - 1]
        except (ValueError, IndexError):
            raise ValueError(f"foto sconosciuta: scegli tra immagine 1 e immagine {len(self.foto)}") from None
        img = ImageOps.exif_transpose(Image.open(percorso)).convert("RGB")
        out, testo = (griglia if nome == "griglia" else zoom)(img, a)
        self.fatte += 1
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=90)
        return [{"type": "text", "text": f"{testo}. Operazioni usate: {self.fatte} di {self.massimo}."},
                {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/jpeg"}]

    def rispondi(self, msg):
        metodo, ident = msg.get("method"), msg.get("id")
        if ident is None:
            return None  # notifiche: nessuna risposta
        if metodo == "initialize":
            versione = msg.get("params", {}).get("protocolVersion", "2025-06-18")
            return {"protocolVersion": versione, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "foto", "version": "1"}}
        if metodo == "tools/list":
            return {"tools": STRUMENTI}
        if metodo == "tools/call":
            p = msg.get("params", {})
            try:
                return {"content": self.chiama(p.get("name"), p.get("arguments") or {}), "isError": False}
            except (ValueError, OSError) as e:
                return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        if metodo == "ping":
            return {}
        raise LookupError(metodo)

    def servi(self):
        sys.stdin.reconfigure(encoding="utf-8")  # su Windows la codifica predefinita non è UTF-8
        for riga in sys.stdin:
            try:
                msg = json.loads(riga)
            except ValueError:
                continue
            try:
                risultato = self.rispondi(msg)
                if risultato is None:
                    continue
                risposta = {"jsonrpc": "2.0", "id": msg["id"], "result": risultato}
            except LookupError as e:
                risposta = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"metodo sconosciuto: {e}"}}
            sys.stdout.write(json.dumps(risposta) + "\n")
            sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("foto", nargs="*")
    ap.add_argument("--massimo", type=int, default=4)
    a = ap.parse_args()
    Server(a.foto, a.massimo).servi()


if __name__ == "__main__":
    main()
