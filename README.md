# QGIS_Tips
# Gestione TAF e DIS con download monografie dei Punti Fiduciali Catastali

**AdE_TAF_DIS** — Progetto QGIS per la gestione dei Punti Fiduciali Catastali
> 🔗 Link diretto : https://bettellam.github.io/QGIS_Tips/AdE_TAF_DIS/README.md
>
> 🔗 Link diretto PDF: https://bettellam.github.io/QGIS_Tips/AdE_TAF_DIS/AdE_TAF_DIS_manuale.pdf
---
Uno degli aspetti che i tecnici devono affrontare nelle operazioni catastali è reperire velocemente le informazioni riguardanti i Punti Fiduciali su cui appoggiare i propri rilievi.

L'Agenzia delle Entrate attraverso gli Uffici Provinciali Territorio mette a disposizione nel proprio sito web:

- **TAF** — **T**abella **A**ttuale dei punti **F**iduciali
- **DIS** — Mutue **DIS**tanze dei punti Fiduciali

Con questo progetto QGIS vengono importate dette tabelle provinciali in GeoPackage per una agevole consultazione dei **PF** presenti sul territorio e relative distanze misurate.

Il sistema predisposto consente di posizionare TAF e DIS in modo geometricamente coerente alle varie origini catastali in Cassini-Soldner / Gauss-Boaga per una *corretta* rappresentazione a scala comunale tramite apposito script di filtro ed impostazione del CRS personalizzato.

Con un semplice click del mouse sul singolo **PF** è possibile eseguire il download della monografia senza dover interagire direttamente dal sito dell'Agenzia delle Entrate nelle operazioni di ricerca:
[https://www1.agenziaentrate.gov.it/servizi/Monografie/ricerca.php](https://www1.agenziaentrate.gov.it/servizi/Monografie/ricerca.php)

Con il download viene estratta dal PDF la prima foto presente, che verrà integrata nel Display HTML Map Tip predisposto.
<div style="display:flex; gap:1rem; flex-wrap:wrap;">
  <img src="AdE_TAF_DIS/img/image001.png" alt="Copy Style → Actions" style="width:80%;" /><br>&nbsp;&nbsp;&nbsp;
</div>

> 🔗 Link diretto : https://bettellam.github.io/QGIS_Tips/AdE_TAF_DIS/README.md
>
> 🔗 Link diretto PDF: https://bettellam.github.io/QGIS_Tips/AdE_TAF_DIS/AdE_TAF_DIS_manuale.pdf
---
## Simulate Water Flow
Semplice utility implementata in azioni di QGis che individua la linea di deflusso dell'acqua su un versante.
![image](https://github.com/bettellam/QGIS_Tips/assets/23143342/16bf10ae-1dca-406d-93de-956dbb0b44b5)

Due le azioni disponibili per il Layer **area_update_etrs89**:
- **WATER_FLOW_Form_Setup**
 Si tratta di un form dove vengono settati i parametri di calcolo: DTM, UP/DOWN, STEP di calcolo.  
I parametri sono memorizzati in variabili utente nel progetto corrente: 
![image](https://github.com/bettellam/QGIS_Tips/assets/23143342/8952d823-9393-4abe-ba10-87f7b162b945)

- **WATER_FLOW_Simulate_D-8**
 Esegue il calcolo della linea di deflusso utilizzando il modello Flow Direction D-8 (celle adiacenti).

**ATTENZIONE:**
- Per ottenere risultati dettagliati, utilizzare un **DTM** sul qual è stato eseguito un **Fill Sinks** ( a tal proposito è possibile utilizzare SAGA o TauDEM di Tarboton).
- Con Direzione **DOWN** : viene individuata la **linea di impluvio** partente dal punto di click con il mouse ( massima pendenza verso valle, linea di deflusso).
- Con Direzione **UP** : viene individuata la **linea di displuvio** partente dal punto di click con il mouse ( massima pendenza verso monte).
- I file generati (LineStringZ) **water_flow_d8_xxxx** vengono caricati automaticamente nel LayerGroup impostato (variabile: **WATER_FLOW_RESULT_Group**).
- L'utente dovrà salvare i file  **water_flow_d8_xxxx** nel modo più opportuno in quanto sono generati in **memory** !
- Il Layer **area_update_etrs89** è un poligono che copre il territorio della Regione del Veneto, l'utente deve modificare tale poligono per adattarlo alla sua area di lavoro/interesse!!
