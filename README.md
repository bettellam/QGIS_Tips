# QGIS_Tips

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
