# Assistente controlli: guida rapida

## 1. Installazione
- Scaricate il file `.exe` dall'[ultima versione](https://github.com/Prot10/assistente-controlli-sorridi/releases/latest) e apritelo.
- Se Windows mostra "Windows ha protetto il PC", cliccate **Ulteriori informazioni**, poi **Esegui comunque**.
- Gli aggiornamenti si installano da soli.

## 2. Cosa fa
- Riceve i messaggi della linea WhatsApp dei controlli.
- Per ogni foto, ChatGPT valuta se l'allineatore calza, seguendo la guida "Gestione degli allineatori", e propone la risposta.
- L'operatore la **invia**, la **modifica** o la **scarta**. Nulla parte da solo, a meno di attivare l'invio automatico.
- Lo **Storico** mostra quante proposte vengono accettate senza modifiche.

## 3. Collegare ChatGPT
- **Impostazioni → ChatGPT → Accedi con ChatGPT**, con l'account aziendale. Serve un piano che includa Codex, per esempio Plus o Team.
- In **Impostazioni → Valutazione → Esempi della guida**, caricate le 4 foto di esempio (inviate a parte), una per grado.

## 4. Collegare WhatsApp
**Serve:** un numero dedicato che non sia attivo su WhatsApp. Il numero dell'attuale app WhatsApp Business non si può usare.

**Su Meta** (una volta sola, circa 45 minuti, da parte dell'IT). Tutti i dettagli sono nella [guida completa](collegare-whatsapp.md).
1. Su developers.facebook.com, create un'app con il caso d'uso "Connettiti con i clienti tramite WhatsApp".
2. Aggiungete il numero e verificatelo con il codice SMS.
3. In WhatsApp Manager, aggiungete un metodo di pagamento.
4. In Impostazioni azienda, create un utente di sistema. Assegnategli l'app e l'account WhatsApp, poi generate un token con scadenza **Mai** e i permessi `whatsapp_business_messaging` e `whatsapp_business_management`.
5. Copiate l'**app secret** da Impostazioni app → Di base.
6. Inserite l'URL della privacy policy e portate l'app in modalità **Live**.

**Nell'assistente** (Impostazioni → WhatsApp): cinque passaggi, ognuno diventa verde quando Meta conferma.
1. Incollate **token** e **app secret**, poi premete **Verifica**.
2. Scegliete il **numero**. Se l'app chiede l'**ID dell'account WhatsApp**, lo trovate su business.facebook.com → Impostazioni → Account → Account di messaggistica, sotto il nome dell'account.
3. **Attivate** il numero con un PIN di 6 cifre.
4. **Ricezione**: copiate URL e token di verifica in Meta → WhatsApp → Configurazione → Webhook, poi attivate il campo **messages**. Va fatto una volta sola, poi l'app si aggiorna da sola.
5. **Prova**: scrivete al numero da un altro telefono. Il messaggio compare in Richieste.

## 5. Costi
- I messaggi in arrivo sono sempre gratuiti.
- Le risposte sono **gratuite fino a 1.000 al mese** per numero. Oltre, costano pochi centesimi ciascuna.
- Il tunnel incluso (Cloudflare) è gratuito.

## 6. Da sapere
- Per ricevere i messaggi, il PC deve restare acceso con l'app aperta.
- Dopo 24 ore dall'ultimo messaggio del paziente, WhatsApp non accetta più risposte libere. L'app lo segnala.
