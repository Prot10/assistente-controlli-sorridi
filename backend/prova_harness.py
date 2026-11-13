#!/usr/bin/env python3
"""Misura l'app sulle foto di prova: ogni foto passa da motore.analizza, cioè lo stesso percorso di un messaggio vero.

Le foto (deidentificate), le etichette e gli esempi della guida non stanno in questo repository: sono dati di pazienti.
Ogni foto va a OpenAI con l'account ChatGPT di `codex login`, quindi serve --confermo-invio.

    python3 prova_harness.py --confermo-invio --foto FOTO --chiave chiave.json --esempi ESEMPI --uscita USCITA
    python3 prova_harness.py --solo-punteggi --chiave chiave.json --uscita USCITA

chiave.json: {"<id foto>": {"label": "ok" | "ko" | null}}. --escludi toglie dai punteggi le foto che compaiono
anche tra gli esempi (il 4 Nov 2026: img_4308, img_3120, img_2213, img_2705).
"""

import argparse
import json
import statistics as st
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from archivio import ISTRUZIONI_INIZIALI
from motore import GRADI_ESEMPI, Rifiutato, analizza

TESTO = "Foto di controllo dopo 7 giorni di uso dell'allineatore HARD."


def valuta(foto, file, esempi, sforzo, istruzioni):
    try:
        r = analizza([{"quando": datetime.now(), "testo": TESTO, "immagine": str(foto)}], sforzo, foto_esempi=esempi,
                     istruzioni=istruzioni)
    except (Rifiutato, Exception) as e:  # noqa: BLE001 - l'errore resta nel file e la foto non conta
        r = {"errore": str(e)[:500]}
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    return file


def metriche(pred, vero):
    """Positivo = 'ko'; segnalato = tutto ciò che non è OK, perché va a un operatore."""
    tp = fp = fn = tn = 0
    for i, v in vero.items():
        if v is None or i not in pred:
            continue
        s = pred[i] != "OK"
        tp, fp = tp + (s and v == "ko"), fp + (s and v == "ok")
        fn, tn = fn + (not s and v == "ko"), tn + (not s and v == "ok")
    return {"fn": fn, "fp": fp, "recall": tp / (tp + fn) if tp + fn else 0, "specificita": tn / (tn + fp) if tn + fp else 0}


def punteggi(uscita, vero, escludi):
    runs = sorted(p for p in uscita.iterdir() if p.is_dir() and p.name.startswith("run"))
    per_run, ops, sec, tok, errori = [], [], [], [], 0
    for dr in runs:
        pred = {}
        for f in dr.glob("*.json"):
            r = json.loads(f.read_text())
            if "errore" in r:
                errori += 1
                continue
            pred[f.stem] = r["esito"]["decisione"]
            ops.append(sum(o["riuscita"] for o in r["operazioni"]))
            sec.append(r["secondi"])
            tok.append(r["token"].get("input_tokens", 0) + r["token"].get("output_tokens", 0))
        per_run.append(pred)
    v = {i: l for i, l in vero.items() if i not in escludi and l}
    ms = [metriche(p, v) for p in per_run]
    righe = [f"{len(runs)} run, {errori} errori, {len(v)} foto con etichetta ({sum(l == 'ko' for l in v.values())} non OK)",
             f"mancati {[m['fn'] for m in ms]}  falsi allarmi {[m['fp'] for m in ms]}  "
             f"recall {st.mean(m['recall'] for m in ms):.2f}  specificità {st.mean(m['specificita'] for m in ms):.2f}",
             f"decisioni {dict(Counter(d for p in per_run for d in p.values()))}  operazioni per foto "
             f"{st.mean(ops) if ops else 0:.1f}  tempo mediano {st.median(sec) if sec else 0:.0f} s  "
             f"token medi {round(st.mean(tok)) if tok else 0}"]
    (uscita / "punteggi.txt").write_text("\n".join(righe) + "\n")
    print("\n".join(righe))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--foto", type=Path)
    ap.add_argument("--chiave", type=Path, required=True)
    ap.add_argument("--esempi", type=Path, help="cartella con guida-grado-1..4.jpg; senza, la prova va senza esempi")
    ap.add_argument("--uscita", type=Path, required=True)
    ap.add_argument("--escludi", nargs="*", default=[])
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--sforzo", default="medium")
    ap.add_argument("--istruzioni", type=Path, help="istruzioni dello staff; senza, la bozza iniziale come nell'app")
    ap.add_argument("--parallelo", type=int, default=4)
    ap.add_argument("--confermo-invio", action="store_true", help="le foto dei pazienti vanno a OpenAI")
    ap.add_argument("--solo-punteggi", action="store_true")
    a = ap.parse_args()
    vero = {i: v["label"] for i, v in json.loads(a.chiave.read_text()).items()}
    if not a.solo_punteggi:
        if not a.confermo_invio or not a.foto:
            raise SystemExit("Servono --foto (deidentificate) e --confermo-invio, dopo l'ok di Andrea.")
        istruzioni = a.istruzioni.read_text(encoding="utf-8") if a.istruzioni else ISTRUZIONI_INIZIALI
        esempi = [a.esempi / f"guida-grado-{g}.jpg" for g in GRADI_ESEMPI] if a.esempi else []
        lavori = [(f, a.uscita / f"run{k}" / f"{f.stem}.json") for k in range(1, a.run + 1) for f in sorted(a.foto.glob("*.jpg"))]
        lavori = [l for l in lavori if not l[1].exists() or "errore" in json.loads(l[1].read_text())]
        print(f"{len(lavori)} valutazioni da fare", flush=True)
        with ThreadPoolExecutor(a.parallelo) as ex:
            for n, f in enumerate(ex.map(lambda l: valuta(l[0], l[1], esempi, a.sforzo, istruzioni), lavori), 1):
                r = json.loads(f.read_text())
                print(f"{n}/{len(lavori)} {f.stem} {r['esito']['decisione'] if 'esito' in r else 'ERRORE ' + r['errore'][:80]}", flush=True)
    punteggi(a.uscita, vero, set(a.escludi))


if __name__ == "__main__":
    main()
