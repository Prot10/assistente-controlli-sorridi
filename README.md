# Assistente controlli

App desktop per Windows e Mac al servizio della linea WhatsApp di controllo di Allineatori Sorridi. Quando arriva un messaggio:
1. lo riceve dalla WhatsApp Business Platform di Meta;
2. riconosce se è una foto di controllo, una domanda o altro;
3. fa valutare la foto a GPT-6.1 Sol attraverso Codex e l'abbonamento ChatGPT di chi usa l'app; il modello dà un grado a ogni dente frontale e l'azione della guida "Gestione degli allineatori";
4. propone la risposta.

L'operatore la invia così com'è, la modifica oppure la scarta. Tutto resta nello storico. Quando lo storico mostra che le proposte vengono accettate senza modifiche, si può attivare l'invio automatico per i casi sicuri.

## Installazione
In breve, per chi la usa: [docs/guida-rapida.md](docs/guida-rapida.md).

Scaricare l'ultima versione dalla pagina [Releases](https://github.com/Prot10/assistente-controlli-sorridi/releases/latest):
- **Windows**: `assistente-controlli-…-win-x64.exe`. L'installer non è firmato: Windows può mostrare "Windows ha protetto il PC"; scegliere "Ulteriori informazioni", poi "Esegui comunque".
- **Mac** (Apple Silicon): `assistente-controlli-…-mac-arm64.dmg`, firmato e notarizzato.

L'app si aggiorna da sola: controlla le nuove versioni all'avvio e ogni 4 ore, le scarica in background e chiede di riavviare.

## Cosa serve
- Un account ChatGPT con accesso a Codex.
- Un numero dedicato sulla **WhatsApp Business Platform (Cloud API)** di Meta, con un token permanente di un utente di sistema e l'app secret. Come prepararlo: [docs/collegare-whatsapp.md](docs/collegare-whatsapp.md), insieme ai costi (gratis fino a 1.000 risposte al mese per numero).

## Prima configurazione
In **Impostazioni**, divise in quattro schede:
- **WhatsApp**: il collegamento in cinque passaggi, qui sotto, e le impostazioni avanzate del tunnel.
- **ChatGPT**: "Accedi con ChatGPT" (si apre il browser) oppure "Accedi con un codice".
- **Valutazione**: modello, livello di ragionamento ed **esempi della guida**. Caricare le 4 foto di esempio della guida "Gestione degli allineatori", una per grado. Il modello le confronta con la foto del paziente. Non sono nel repository perché sono foto di pazienti: Sorridi le riceve a parte. Finché ne manca anche una, ogni foto ricevuta passa da un operatore e l'invio automatico resta fermo: nella prova del 13 novembre 2026, senza esempi, una foto non OK è stata giudicata OK.
- **Invio**: il nome dell'operatore che firma gli invii e l'invio automatico.

I cinque passaggi di **WhatsApp**, ciascuno controllato su Meta:
  1. Token e app secret: l'assistente ricava da solo l'app, l'account WhatsApp e i numeri.
  2. Scelta del numero.
  3. Attivazione del numero sulla Cloud API con il PIN.
  4. Ricezione: tunnel e webhook. Il webhook nel pannello Meta si imposta una volta sola, poi l'assistente comunica da solo a Meta l'indirizzo nuovo a ogni avvio.
  5. Messaggio di prova.

Il tunnel è incluso: cloudflared apre un tunnel veloce di Cloudflare, senza account, che espone solo la porta del webhook; la pagina dell'assistente resta raggiungibile solo dal computer. Nelle impostazioni avanzate si può usare invece un tunnel vostro con indirizzo fisso.

Su Mac, chiudere la finestra non chiude l'app: il webhook continua a ricevere. Per fermarla, Esci dal menu.

## Uso
La pagina usa la grafica e i componenti del nuovo CRM Sorridi. La barra laterale porta a Richieste, Chat, Istruzioni, Storico e Impostazioni, e mostra se ChatGPT e WhatsApp sono collegati e se l'invio automatico è attivo; si stringe dal pulsante accanto al logo. In alto ci sono la ricerca (nome, numero o paziente, in Richieste e in Chat), il tema chiaro o scuro e le iniziali dell'operatore.

- **Richieste**: i messaggi in arrivo, divisi per caso. L'analisi parte dopo un'attesa di silenzio (2 minuti all'inizio, da 20 secondi a 15 minuti in Impostazioni → Valutazione), perché spesso il medico scrive prima e manda le foto dopo. Durante l'attesa la richiesta mostra il conto alla rovescia, le foto scaricate e **Analizza ora**; durante l'analisi mostra cosa sta facendo l'agente. **Non analizzare** e **Ferma l'analisi** lasciano la richiesta aperta per una risposta a mano. Un'analisi interrotta dalla chiusura dell'app riparte alla riapertura.
- **Più casi dallo stesso numero**: prima di valutare, una chiamata leggera al modello divide i messaggi per paziente. Due pazienti mandati di fila diventano due richieste, ognuna con la sua risposta e il nome del paziente. Un messaggio che completa una richiesta ancora da inviare (la foto dimenticata, «ecco l'altra») ci viene spostato e la richiesta si rianalizza. Se la richiesta era già chiusa, ne nasce una nuova con «Segue la richiesta N»: il modello rivede anche i messaggi e le foto di allora. Nel dubbio non divide e non collega. Ogni spostamento finisce nel registro e nello zip.
- Ogni proposta mostra decisione, grado, motivo, azione della guida e una risposta completa, pronta da inviare. Quando il caso va seguito da una persona compare il riquadro rosso **Serve un operatore**: la risposta è comunque scritta come per l'invio. Le azioni sono Invia, Scarta e Rianalizza; "Decisione corretta" registra il giudizio dell'operatore.
- **Chat**: le conversazioni complete con lo stato di consegna e un campo per i messaggi liberi (Invio manda, Maiuscolo+Invio va a capo). L'elenco mostra l'ultimo messaggio e le richieste aperte di ogni contatto; le foto mandate insieme formano un album. Ogni chat si può **rinominare**, **archiviare** (torna tra le chat se il contatto riscrive) o **cancellare** da questo PC.
- **Notifiche** (Impostazioni → Notifiche): avvisi di Windows per tutti i messaggi, solo per le proposte pronte, solo quando serve un operatore, oppure mai, con o senza suono. Un clic sull'avviso apre la richiesta.
- **Foto**: nella richiesta sono miniature numerate come le cita il modello («immagine 2»). Un clic apre il visore: frecce tra le foto, zoom con rotella o doppio clic, trascinamento, più contrasto e rotazione.
- **Istruzioni**: come l'assistente deve scrivere le risposte proposte (tono, domande frequenti, quando passare a un operatore). Si parte da una bozza modificabile; non cambiano la valutazione delle foto.
- **Storico**: quante proposte sono state accettate senza modifiche, modificate o scartate, e quanto spesso l'operatore conferma la decisione del modello. Si può esportare in CSV, oppure scaricare **tutti i dati (zip)**: una cartella per richiesta con messaggi, foto, valutazione, risposta proposta e inviata, parole cambiate, decisione del modello e dell'operatore, più il registro di ogni azione (analisi, invii, scarti, chat, impostazioni).
- **Invio automatico**: spento all'inizio. Quando è attivo, manda da solo solo le foto di controllo con una decisione ammessa, confidenza sopra la soglia e senza richiesta di operatore.

Dopo 24 ore dall'ultimo messaggio del paziente, WhatsApp accetta solo messaggi modello approvati da Meta. L'assistente lo segnala.

## Come valuta
La valutazione è un piccolo harness costruito per questo compito, in `backend/harness/`:
- `messaggi.md`: tipi di messaggio, azioni della guida per grado, regole della risposta suggerita;
- `valuta-fit.md`: scala da 0 a 3 per dente (banda incisale rispetto alla corona) e tabella di decisione;
- `ispeziona-foto.md`: come usare gli strumenti sulla foto;
- `esempi.md`: i 4 esempi della guida, allegati dopo la foto del paziente;
- `istruzioni-sorridi.md`: la bozza iniziale della pagina Istruzioni; il testo salvato dallo staff entra nel prompt solo per la risposta suggerita;
- `schema.json`: la risposta, con i gradi dente per dente.

Il modello può solo chiamare `griglia` e `zoom` sulle foto del paziente (`backend/strumenti_foto.py`, al massimo 4 operazioni). Tutti gli altri strumenti di Codex sono spenti; se il modello prova a usarne uno, l'esito viene scartato.

Sulle 37 foto di prova di Sorridi (21 da segnalare), Sol con questo harness ha mancato 2-3 foto da segnalare e dato 5-6 falsi allarmi, in linea con Claude Opus sulle stesse foto. `backend/prova_harness.py` ripete la misura; foto ed etichette non sono nel repository.

## Dati e sicurezza
- Storico, foto, esempi della guida e login ChatGPT stanno in `%LOCALAPPDATA%\AssistenteSorridi` (su Mac in `~/Library/Application Support/AssistenteSorridi`). Contengono dati di pazienti: vanno inclusi nel backup del computer e non vanno copiati altrove. Il registro dell'app è in `%APPDATA%\Assistente controlli\logs` (su Mac in `~/Library/Logs/Assistente controlli`).
- Lo zip dello Storico contiene foto e messaggi dei pazienti, senza le chiavi di Meta: va mandato solo per canali riservati. Cancellare una chat toglie messaggi, richieste, foto e registro di quella chat; resta solo la riga che dice che è stata cancellata.
- La pagina ascolta solo su 127.0.0.1 e rifiuta richieste da altri siti. Il webhook accetta solo messaggi firmati da Meta con l'app secret.
- I messaggi dei pazienti sono trattati come dati: chi chiede di ignorare le regole finisce all'operatore.
- Testi e foto vanno a OpenAI con l'account ChatGPT collegato. Conservazione e uso dei dati seguono il piano e le impostazioni di quell'account: va verificato prima dell'uso con pazienti.

## Sviluppo
```
backend/     il servizio Python: pagina, webhook, valutazione (assistente.py, motore.py, whatsapp.py, tunnel.py, archivio.py)
backend/statico/crm/  stili, caratteri e logo copiati senza modifiche dal nuovo CRM (crm-nuovo/packages/ui e la cornice dell'app)
electron/    il guscio desktop: avvia il servizio, apre la finestra, aggiornamenti
scripts/     build del servizio (PyInstaller), download di Codex e cloudflared, rilascio Mac
docs/        guida al collegamento di WhatsApp
```

Avvio in sviluppo (Python 3.11 con `backend/requirements.txt`, Node 22; Codex arriva da npx):
```bash
npm install
npm run cloudflared
npm start
```

Il solo servizio, nel browser:
```bash
python3 backend/assistente.py
```

`backend/prova/finto_meta.py` fa da Meta finta per provare tutto senza un numero vero, compresa la configurazione guidata: il numero parte da attivare e il webhook del pannello si imposta con `finto_meta.py pannello`. Con la Meta finta usate il tunnel personalizzato con indirizzo `http://127.0.0.1:<porta webhook>`.

### Rilascio
1. Aumentare `version` in `package.json` e committare.
2. Su un Mac con il Developer ID nel portachiavi e un file `.env` (oppure il percorso in `ENV_FILE`) con `APPLE_ID`, `APPLE_PASSWORD` (password specifica per app) e `APPLE_TEAM_ID`:
   ```bash
   npm run release:mac
   ```
   Lo script crea tag e release, costruisce, firma e notarizza l'app per Mac e la carica. Il tag fa partire GitHub Actions (`.github/workflows/release.yml`), che costruisce l'installer per Windows e lo carica nella stessa release.

## Licenza
Codice sotto licenza MIT (`LICENSE`). Il nome e il logo di Allineatori Sorridi sono marchi di Sorridi e non sono coperti dalla licenza. I caratteri Josefin Sans e Montserrat hanno le loro licenze in `backend/statico/crm/fonts/`. L'app include cloudflared di Cloudflare (Apache 2.0) e Codex di OpenAI (Apache 2.0), così come sono distribuiti.
