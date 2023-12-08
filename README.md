# QGIS_Tips
 
## Simulate Water Flow
Semplice utility implementata in azioni di QGis che individua la linea di deflusso dell'acqua su un versante.
![image](https://github.com/bettellam/QGIS_Tips/assets/23143342/16bf10ae-1dca-406d-93de-956dbb0b44b5)

Due le azioni disponibili per il Layer **area_update_etrs89**:
- **WATER_FLOW_Form_Setup**
 Si tratta di un form dove vengono settati i parametri di calcolo: DTM, UP/DOWN, STEP di calcolo.  
I parametri sono memorizzari in variabili utente nel progetto corrente: 
![image](https://github.com/bettellam/QGIS_Tips/assets/23143342/8952d823-9393-4abe-ba10-87f7b162b945)

- **WATER_FLOW_Simulate_D-8**
 Esegue il calcolo dellaliea di deflusso utilizzando im modello Flow Direction D-8 (celle adiacenti).

**ATTENZIONE:**
Per ottenere risultati più dettagliati, utilizzare un DTM sul qual è stato eseguito un Fill Sinks ( a tal proposito è possibile utilizzare SAGA o TauDEM di Tarboton).
Con Direzione DOWN viene calcolato la linea di impluvio ( massima pendenza verso valle, linea di deflusso).
Con Direzione UP viene calcolato la linea di displuvio ( massima pendenza verso monte).
