# Collegare WhatsApp all'Assistente controlli

Guida per chi segue l'IT. La parte su Meta si fa una volta sola e richiede circa 30-45 minuti; il resto lo fa l'app, che mostra un passaggio alla volta in **Impostazioni → WhatsApp Business** e controlla ogni passaggio interrogando Meta.

L'assistente usa la **WhatsApp Business Platform (Cloud API)** di Meta, cioè l'accesso ufficiale per i programmi. Non è l'app WhatsApp Business del telefono. Meta cambia spesso il proprio pannello, quindi i nomi dei menu possono essere un po' diversi da quelli scritti qui.

## Prima di iniziare
- **Un numero di telefono dedicato** che riceva SMS o chiamate per il codice di verifica (anche un fisso). Il numero **non deve essere attivo su WhatsApp**: né sull'app normale né su WhatsApp Business. Se lo è, prima va eliminato l'account WhatsApp su quel numero (WhatsApp → Impostazioni → Account → Elimina account).
  - Il numero che oggi usate con l'app WhatsApp Business **non si può usare contemporaneamente** con l'assistente. Meta lo permette (si chiama "coexistence") solo passando da un fornitore certificato (Tech Provider).
- Un account Facebook personale di chi amministra, con accesso al **portfolio business** di Sorridi su business.facebook.com (se non esiste, si crea durante il passo 1).
- L'indirizzo della **privacy policy** del sito di Sorridi: serve a Meta per rendere l'app attiva.

## Su Meta

### 1. Creare l'app
1. Andate su developers.facebook.com e accedete. Al primo accesso Meta chiede di registrarsi come sviluppatore: è gratuito.
2. **Le mie app → Crea app**. Come caso d'uso scegliete **"Connettiti con i clienti tramite WhatsApp"**, poi collegate il portfolio business di Sorridi.
3. Meta crea un account WhatsApp (WABA) e un numero di prova. Il numero di prova non serve all'assistente.

### 2. Aggiungere il numero vero
1. Nell'app: **WhatsApp → Configurazione API → Aggiungi numero di telefono**.
2. Inserite il **nome visualizzato** (quello che vedono i pazienti, per esempio "Allineatori Sorridi"), la categoria e il numero.
3. Confermate con il codice via SMS o chiamata.
4. Meta deve approvare il nome visualizzato, di solito entro un paio di giorni. Intanto si può andare avanti. Se l'attivazione del numero nell'assistente (passaggio 3) dà errore, aspettate l'approvazione e riprovate.

### 3. Metodo di pagamento
In **WhatsApp Manager** (business.facebook.com → WhatsApp Manager) aggiungete un metodo di pagamento all'account WhatsApp. Sotto le 1.000 risposte al mese non viene addebitato niente, ma senza metodo di pagamento le risposte oltre la millesima non partono. Dettagli in [Costi](#costi).

### 4. Utente di sistema e token
Il token è la chiave con cui l'assistente parla con Meta. Deve essere di un **utente di sistema**, perché i token personali scadono dopo poche ore.
1. business.facebook.com → **Impostazioni** (impostazioni dell'azienda) → **Utenti → Utenti di sistema → Aggiungi**: nome per esempio "assistente-controlli", ruolo **Amministratore**.
2. **Assegna risorse**: l'**app** creata al passo 1 e l'**account WhatsApp**, entrambi con controllo completo.
3. **Genera token**: scegliete l'app, scadenza **Mai**, permessi **whatsapp_business_messaging** e **whatsapp_business_management**.
4. Copiate il token: Meta lo mostra una volta sola. Trattatelo come una password.

### 5. App secret
developers.facebook.com → l'app → **Impostazioni app → Di base → Chiave segreta (App secret) → Mostra**. Serve all'assistente per riconoscere i messaggi firmati da Meta.

### 6. Rendere l'app attiva (Live)
1. Nella stessa pagina **Impostazioni app → Di base**, inserite l'URL della privacy policy e salvate.
2. Portate l'app in modalità **Live** (pubblicazione dell'app nella barra in alto o in "Pubblica").

Finché l'app è in modalità sviluppo, Meta non invia all'assistente tutti i messaggi dei pazienti.

## Nell'assistente
Aprite **Impostazioni → WhatsApp Business** e seguite i cinque passaggi. Ognuno diventa verde quando Meta conferma che è a posto.

1. **Token e app secret**: incollateli e premete **Verifica**. L'assistente controlla che il token non scada, che abbia i due permessi e che il secret sia quello dell'app.
2. **Numero**: compaiono i numeri dell'account. Se ce n'è uno solo, viene scelto da solo.
3. **Attivazione del numero**: il numero va attivato sulla Cloud API una volta sola con un **PIN di 6 cifre**. Se il numero ha già la verifica in due passaggi usate quel PIN, altrimenti sceglietene uno e conservatelo con le altre credenziali.
4. **Ricezione dei messaggi**: l'assistente apre da solo un tunnel sicuro (Cloudflare) e mostra due valori, l'**URL di callback** e il **token di verifica**. **Una volta sola**:
   1. Su developers.facebook.com, nell'app, andate su **WhatsApp → Configurazione**. Nelle app nuove la voce è in **Casi d'uso → Personalizza → Configurazione**.
   2. In **Webhook** premete **Modifica**, incollate i due valori e premete **Verifica e salva**.
   3. In **Campi del webhook**, attivate **messages**.
   4. Tornate nell'assistente e premete **Ricontrolla**.

   Da lì in poi, a ogni avvio l'assistente comunica a Meta il nuovo indirizzo da solo.
5. **Messaggio di prova**: da un altro telefono scrivete al numero. Il messaggio compare in **Richieste** e il passaggio diventa verde.

## Costi
Prezzi di Meta in vigore dal 1 ottobre 2026 ([fonte](https://developers.facebook.com/docs/whatsapp/pricing/)):
- **I messaggi dei pazienti verso Sorridi sono sempre gratuiti.**
- Le risposte dell'assistente sono messaggi di servizio, cioè risposte libere entro 24 ore dall'ultimo messaggio del paziente. **Le prime 1.000 al mese per numero sono gratuite**. Il conteggio riparte ogni mese, e quelle non usate non si accumulano.
- Oltre le 1.000, ogni risposta costa pochi centesimi, con la tariffa per l'Italia della [tabella ufficiale](https://whatsappbusiness.com/products/platform-pricing/). Senza metodo di pagamento, Meta smette di consegnare le risposte fino al mese successivo.
- Dopo 24 ore dall'ultimo messaggio del paziente, WhatsApp accetta solo messaggi modello approvati, che si pagano sempre. L'assistente non li manda: segnala che la finestra è chiusa.
- Il tunnel Cloudflare automatico è gratuito e non richiede un account.

Per una linea di controlli con poche decine di risposte al giorno si resta sotto le 1.000 al mese, quindi senza costi.

## Cose da sapere
- **Il PC deve restare acceso con l'assistente aperto** per ricevere i messaggi. Se il webhook non risponde, Meta riprova la consegna per un massimo di 7 giorni. Con il tunnel automatico, però, l'indirizzo cambia a ogni avvio, e non abbiamo verificato se i nuovi tentativi arrivano al nuovo indirizzo. Per non perdere messaggi tenete l'assistente aperto in orario di lavoro, oppure usate un tunnel con indirizzo fisso.
- **Tunnel con indirizzo fisso** (facoltativo): per esempio un tunnel Cloudflare con nome sul dominio di Sorridi, oppure un dominio fisso ngrok. Va in **Impostazioni avanzate → Tunnel → Personalizzato**, con il comando e l'indirizzo pubblico. L'assistente lo comunica a Meta come quello automatico.
- **Sicurezza**:
  - Il tunnel espone solo il webhook, non la pagina dell'assistente.
  - Il webhook accetta solo messaggi firmati da Meta con l'app secret.
  - Token e secret restano sul PC, nella cartella dati dell'assistente.
- **Se un passaggio resta arancione**, la riga con la ✗ dice cosa manca. Il registro dell'app è in `%APPDATA%\Assistente controlli\logs`.
