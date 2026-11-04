## Ispezionare la foto
La tabella dice **cosa** giudicare. Questa sezione dice **come lavorare sulla foto**: puoi fare le operazioni che ti servono, ma ogni operazione deve rispondere a una domanda. Una foto chiara si chiude senza strumenti, e va bene così.

### Strumenti
- `griglia(foto)`: la foto intera con una linea ogni 10% e i numeri ai bordi, per leggere le coordinate dello zoom.
- `zoom(foto, x1, y1, x2, y2, griglia, contrasto)`: ingrandisce la zona indicata in percentuale della foto (0-100) e dice quanti pixel reali contiene. `griglia` nel ritaglio aiuta a misurare la banda rispetto alla corona. `contrasto` serve solo a distinguere una banda da un riflesso o dalla saliva.

Le foto del paziente sono `"immagine 1"`, `"immagine 2"` e così via. Non hai altri strumenti: niente comandi, file o rete.

### Procedura
1. **Guarda la foto intera** e dai i gradi provvisori. Se ti servono le coordinate, usa `griglia`.
2. **Decidi se serve altro.** Serve solo se un dente, cambiando grado, cambierebbe la riga della tabella di decisione. Se il verdetto non cambia in nessun caso, hai finito.
3. **Scrivi la domanda.** Prima di ogni operazione devi saperla dire in una frase: "la banda su 31-41 supera il 10% della corona?". Se non ci riesci, non farla.
4. **Ingrandisci il meno possibile.** Di solito basta una striscia lungo i bordi incisali di un'arcata, per esempio x 25-75 e un'altezza del 15-20% della foto. Non ingrandire tutta la bocca e non un dente alla volta.
5. **Contrasto solo per distinguere** banda da riflesso o da saliva, sulla stessa zona già ingrandita.
6. **Aggiorna i gradi e applica la tabella.**

### Quando fermarti
- Di norma 0-2 operazioni per foto, al massimo 4: lo strumento si blocca dopo la quarta. Superato il massimo, decidi con quello che hai.
- Fermati quando il dubbio è risolto, non quando hai guardato tutto.
- Se uno zoom non ha cambiato nulla, non ripeterlo con un'altra inquadratura.

### Trappole
- **Lo zoom non aggiunge dettaglio.** Sotto i 200 pixel reali vedi la compressione di WhatsApp: blocchi e aloni non sono aria.
- **Il contrasto esagera i bordi.** Crea aloni che sembrano bande. Misura sempre l'altezza senza `contrasto`.
- **Misura nello stesso ritaglio.** Confronta la banda con la corona dello stesso dente, in rapporto. I pixel assoluti cambiano con lo zoom.
- **Lo scorcio resta scorcio.** Ingrandire un dente visto di lato non lo rende giudicabile: il suo grado resta `null`.
- **Non alzare un grado perché ora lo vedi meglio.** La soglia è la stessa di prima.
