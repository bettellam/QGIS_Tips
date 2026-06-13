# -*- coding: utf-8 -*-
"""
ImportaTAF.py  –  QGIS Processing Script
Licenza https://www.gnu.org/licenses/quick-guide-gplv3.html
V09 Giugno 2026 - Mauro Bettella + Claude AI
=========================================
Importa un file .TAF (Tabella Attuale dei punti Fiduciali - Agenzia delle Entrate)
in un GeoPackage, creando un layer punti con tutti i campi del tracciato
ufficiale (versione 15.02.2004) più il campo Codice_TAF per avviare il download
delle monografie tramite lo script AzioneMonografia.py.

Installazione:
  Processing Toolbox → menu ⚙ → Add Script to Toolbox → seleziona questo file

Note sul formato TAF:
  - File a larghezza fissa, 251 caratteri/riga, encoding Latin-1, terminatori CRLF
  - Le coordinate Nord/Est sono metriche e vengono usate come geometria Point.
    Il CRS assegnato di default è EPSG:3003 (Roma40 / Monte Mario Italy zone 1)
    come punto di partenza ragionevole per l'area italiana; nella realtà i fogli
    catastali possono usare sistemi misti (Gauss-Boaga zona 1/2 o Cassini-Soldner
    con origine locale per foglio). 
    L'utente deve impostare il CRS corretto per il proprio comune dopo l'importazione
    (tasto destro layer → Imposta CRS).
    Esempio:G855
    +proj=cass +units=m +k=1 +ellps=bessel +towgs84=518.73805,10.750666,488.892114 +lat_0=45.358967596379 +lon_0=11.8920977331488 +type=crs
    
  - Codice_TAF: formato BELFI-FOFX10-NN  es. 'L900-0010-12'
    (compatibile con AzioneMonografia.py)
    Codice_TAF ESEMPI :
    CODICE | SEZIONE | FOGLIO | ALLEGATO | NUMERO  | Codice TAF
    COMUNE |         |        |          |FIDUCIALE|  COMPLETO
    -----------------------------------------------------------------
    F842   | null    | -032   |   I      |  -52    | F842-032I-52
    G855   | null    | -004   | null=0   |  -04    | G855-0040-04
    E506   |  B      | -267   | null=0   |  -07    | E506B-2670-07

Tracciato di riferimento: TAF – Tabella Attuale dei punti Fiduciali, v. 15.02.2004
"""

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFile,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsProject,
)
from pathlib import Path


# ── Costanti tracciato (posizioni 0-based, lunghezze dal PDF ufficiale) ───────
# Ogni campo è (start, end) in slice notation Python (end escluso)
_CAMPO = {
    'codice_comune':    (0,   4),   # A  4  Codice Belfiore
    'codice_sezione':   (4,   5),   # A  1  Codice sezione (spazio se assente)
    # pos 5: separatore
    'foglio':           (6,  10),   # N  4  Foglio
    # pos 10: separatore
    'allegato':         (11, 12),   # A  1  Allegato (spazio se Allegato='0')
    # pos 12: separatore
    'fiduciale':        (13, 17),   # N  4  Fiduciale
    # pos 17: separatore
    'particella':       (18, 29),   # A 11  Particella (allineata a sinistra)
    # pos 29: separatore
    'mono_planimetrica':(30, 100),  # A 70  Monografia planimetrica
    # pos 100: separatore
    'coord_nord':       (101, 113), # N 12  Coordinata Nord (12.3)
    # pos 113: separatore
    'coord_est':        (114, 126), # N 12  Coordinata Est (12.3)
    # pos 126: separatore
    'attendibilita':    (127, 129), # N  2  Attendibilità planimetrica
    # pos 129: separatore
    'foglio_origine':   (130, 134), # N  4  Foglio origine
    # pos 134: separatore
    'allegato_origine': (135, 136), # A  1  Allegato foglio origine
    # pos 136: separatore
    'fiduciale_origine':(137, 141), # N  4  Fiduciale origine
    # pos 141: separatore
    'data_aggiorn':     (142, 148), # N  6  Data aggiornamento GGMMAA
    # pos 148: separatore
    'causale_aggiorn':  (149, 164), # A 15  Causale aggiornamento
    # pos 164: separatore
    'mono_altimetrica': (165, 235), # A 70  Monografia altimetrica
    # pos 235: separatore
    'attend_altim':     (236, 238), # N  2  Attendibilità altimetrica
                                    # nota: tracciato indica pos 237-239 (1-based)
    # pos 238: separatore
    'quota':            (239, 251), # N 12  Quota (12.3) – 9999.000 se da determinare
}
_ROW_LEN = 251

# CRS di default assegnato al layer (l'utente può cambiarlo dopo)
_DEFAULT_CRS = 'EPSG:3003'  # Roma40 / Monte Mario Italy zone 1


def _parse_row(line: str):
    """
    Parsa una riga del TAF (251 caratteri, già decodificata Latin-1).
    Ritorna un dizionario con tutti i campi + Codice_TAF,
    oppure None se la riga è malformata.
    """
    if len(line) != _ROW_LEN:
        return None

    def _s(key):
        a, b = _CAMPO[key]
        return line[a:b].strip()

    def _int(key):
        v = _s(key)
        if v.lstrip('-').isdigit():
            return int(v)
        return None

    def _float(key):
        try:
            return float(_s(key))
        except (ValueError, TypeError):
            return None

    comune    = _s('codice_comune')
    if not comune:
        return None  # riga vuota/padding

    sezione   = _s('codice_sezione')
    foglio    = _int('foglio')
    allegato  = _s('allegato')
    fiduciale = _int('fiduciale')
    particella        = _s('particella')
    mono_planimetrica = _s('mono_planimetrica')
    coord_nord = _float('coord_nord')
    coord_est  = _float('coord_est')
    attendibilita     = _int('attendibilita')
    foglio_origine    = _int('foglio_origine')
    allegato_origine  = _s('allegato_origine')
    fiduciale_origine = _int('fiduciale_origine')
    data_aggiorn      = _s('data_aggiorn')
    causale_aggiorn   = _s('causale_aggiorn')
    mono_altimetrica  = _s('mono_altimetrica')
    attend_altim      = _int('attend_altim')
    quota             = _float('quota')

    # Codice_TAF: formato BELFI-FOFX10-NN
    # Compatibile con ScaricaMonografie.py (regex r'^([A-Z]\d{3})-(\d{4})-(\d{2})$')
    # ATTENZIONE: per foglio con allegato lettera (es. All='A') il codice
    # è costruito senza allegato – potrebbe non trovare la monografia.
    fog_int  = foglio if foglio is not None else 0
    fid_int  = fiduciale if fiduciale is not None else 0
    all_fog  = allegato if allegato else '0'
    codice_taf = f"{comune}{sezione}-{fog_int:03d}{all_fog}-{fid_int:02d}"

    return {
        'Codice_Comune':    comune,
        'Codice_Sezione':   sezione,
        'Foglio':           foglio,
        'Allegato':         allegato,
        'Fiduciale':        fiduciale,
        'Particella':       particella,
        'Mono_Planimetrica':mono_planimetrica,
        'Coord_Nord':       coord_nord,
        'Coord_Est':        coord_est,
        'Attendibilita':    attendibilita,
        'Foglio_Origine':   foglio_origine,
        'Allegato_Origine': allegato_origine,
        'Fiduciale_Origine':fiduciale_origine,
        'Data_Aggiorn':     data_aggiorn,
        'Causale_Aggiorn':  causale_aggiorn,
        'Mono_Altimetrica': mono_altimetrica,
        'Attend_Altim':     attend_altim,
        'Quota':            quota,
        'Codice_TAF':       codice_taf,
        # Campi monografia — vuoti all'importazione, popolati da AzioneMonografia
        'Accesso':          None,
        'Piano_Paragone':   None,
        'Note':             None,
        'Origine':          None,
        'Istituito':        None,
        'Verificato':       None,
        'Annullato':        None,
        'GB_Nord':          None,
        'GB_Est':           None,
        'GB_Fuso':          None,
        'LAT':              None,
        'LON':              None,
        'H_Ellissoidica':   None,
        'UTM2000_Nord':     None,
        'UTM2000_Est':      None,
        'Foto':             None,
    }


def _build_fields() -> QgsFields:
    """Costruisce la definizione dei campi QgsFields."""
    fields = QgsFields()
    fields.append(QgsField('Codice_Comune',    QVariant.String, len=4))
    fields.append(QgsField('Codice_Sezione',   QVariant.String, len=1))
    fields.append(QgsField('Foglio',           QVariant.Int))
    fields.append(QgsField('Allegato',         QVariant.String, len=1))
    fields.append(QgsField('Fiduciale',        QVariant.Int))
    fields.append(QgsField('Particella',       QVariant.String, len=11))
    fields.append(QgsField('Mono_Planimetrica',QVariant.String, len=70))
    fields.append(QgsField('Coord_Nord',       QVariant.Double))
    fields.append(QgsField('Coord_Est',        QVariant.Double))
    fields.append(QgsField('Attendibilita',    QVariant.Int))
    fields.append(QgsField('Foglio_Origine',   QVariant.Int))
    fields.append(QgsField('Allegato_Origine', QVariant.String, len=1))
    fields.append(QgsField('Fiduciale_Origine',QVariant.Int))
    fields.append(QgsField('Data_Aggiorn',     QVariant.String, len=6))
    fields.append(QgsField('Causale_Aggiorn',  QVariant.String, len=15))
    fields.append(QgsField('Mono_Altimetrica', QVariant.String, len=70))
    fields.append(QgsField('Attend_Altim',     QVariant.Int))
    fields.append(QgsField('Quota',            QVariant.Double))
    fields.append(QgsField('Codice_TAF',       QVariant.String, len=15))
    # ── Campi popolati dalla monografia PDF ──────────────────────────────────
    fields.append(QgsField('Accesso',          QVariant.String, len=70))
    fields.append(QgsField('Piano_Paragone',   QVariant.String, len=70))
    fields.append(QgsField('Note',             QVariant.String, len=70))
    fields.append(QgsField('Origine',          QVariant.String, len=70))
    fields.append(QgsField('Istituito',        QVariant.String, len=10))
    fields.append(QgsField('Verificato',       QVariant.String, len=10))
    fields.append(QgsField('Annullato',        QVariant.String, len=10))
    fields.append(QgsField('GB_Nord',          QVariant.Double))
    fields.append(QgsField('GB_Est',           QVariant.Double))
    fields.append(QgsField('GB_Fuso',          QVariant.String, len=10))
    fields.append(QgsField('LAT',              QVariant.Double))
    fields.append(QgsField('LON',              QVariant.Double))
    fields.append(QgsField('H_Ellissoidica',   QVariant.Double))
    fields.append(QgsField('UTM2000_Nord',     QVariant.Double))
    fields.append(QgsField('UTM2000_Est',      QVariant.Double))
    fields.append(QgsField('Foto',             QVariant.String, len=200))
    return fields


# ── Processing Algorithm ──────────────────────────────────────────────────────

class ImportaTAF(QgsProcessingAlgorithm):

    INPUT_TAF  = 'INPUT_TAF'
    ADD_LAYER  = 'ADD_LAYER'
    APPLY_QML  = 'APPLY_QML'

    def tr(self, s):
        return QCoreApplication.translate('ImportaTAF', s)

    def createInstance(self):
        return ImportaTAF()

    def name(self):
        return 'importataf'

    def displayName(self):
        return self.tr('Importa file TAF in GeoPackage')

    def group(self):
        return self.tr('Catasto')

    def groupId(self):
        return 'catasto'

    def shortHelpString(self):
        return self.tr(
            'Importa un file .TAF (Tabella Attuale dei punti Fiduciali)\n'
            'in un layer punti GeoPackage con tutti i campi del tracciato ufficiale.\n\n'
            'Viene creato il campo <b>Codice_TAF</b> — esempi:\n\n'
            '<table style="font-family:monospace;font-size:small;border-collapse:collapse;">'
            '<tr style="background:#dde;">'  
            '<th style="padding:2px 6px;">Comune</th>'
            '<th style="padding:2px 6px;">Sez.</th>'
            '<th style="padding:2px 6px;text-align:right;">Foglio</th>'
            '<th style="padding:2px 6px;">All.</th>'
            '<th style="padding:2px 6px;text-align:right;">Fid.</th>'
            '<th style="padding:2px 6px;">Codice TAF</th></tr>'
            '<tr>'
            '<td style="padding:2px 6px;">F842</td><td></td>'
            '<td style="padding:2px 6px;text-align:right;">032</td>'
            '<td style="padding:2px 6px;">I</td>'
            '<td style="padding:2px 6px;text-align:right;">52</td>'
            '<td style="padding:2px 6px;"><b>F842-032I-52</b></td></tr>'
            '<tr style="background:#eef;">'
            '<td style="padding:2px 6px;">G855</td><td></td>'
            '<td style="padding:2px 6px;text-align:right;">004</td>'
            '<td style="padding:2px 6px;">0</td>'
            '<td style="padding:2px 6px;text-align:right;">04</td>'
            '<td style="padding:2px 6px;"><b>G855-0040-04</b></td></tr>'
            '<tr>'
            '<td style="padding:2px 6px;">E506</td>'
            '<td style="padding:2px 6px;">B</td>'
            '<td style="padding:2px 6px;text-align:right;">267</td>'
            '<td style="padding:2px 6px;">0</td>'
            '<td style="padding:2px 6px;text-align:right;">07</td>'
            '<td style="padding:2px 6px;"><b>E506B-2670-07</b></td></tr>'
            '</table>\n\n'
            'Compatibile con <i>AzioneMonografia.py</i> per scaricare '
            "le monografie dall'Agenzia delle Entrate.\n\n"
            'Il GeoPackage viene salvato nella stessa cartella del .TAF '
            'con lo stesso nome base (es. PADOVA.gpkg).\n\n'
            '<b>Geometria:</b> punti da Coord_Est (X) e Coord_Nord (Y).<br>'
            'CRS default: <b>EPSG:3003</b>. Modificare dopo l\'importazione con:<br>'
            '→ <i>Properties → Source → Assigned CRS → Custom CRS → Proj String</i><br><br>'
            'Esempio per G855:<br>'
            '<tt>+proj=cass +units=m +k=1 +ellps=bessel '
            '+towgs84=518.73805,10.750666,488.892114 '
            '+lat_0=45.358967596379 +lon_0=11.8920977331488 +type=crs</tt>'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                self.INPUT_TAF,
                self.tr('File TAF (.taf)'),
                extension='taf'
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_LAYER,
                self.tr('Aggiungi il layer al progetto dopo l\'importazione'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.APPLY_QML,
                self.tr('Applica stile QML (QML/TAF.qml)'),
                defaultValue=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        taf_path  = self.parameterAsFile(parameters, self.INPUT_TAF, context)
        add_layer = self.parameterAsBool(parameters, self.ADD_LAYER, context)
        apply_qml = (self.parameterAsBool(parameters, self.APPLY_QML, context)
                     if add_layer else False)

        taf_path = Path(taf_path)
        if not taf_path.exists():
            raise QgsProcessingException(f'File non trovato: {taf_path}')
        if taf_path.suffix.lower() != '.taf':
            raise QgsProcessingException(
                f'Il file deve avere estensione .taf: {taf_path}')

        # GeoPackage nella stessa cartella, stesso nome base
        gpk_path   = taf_path.with_suffix('.gpkg')
        table_name = taf_path.stem  # es. 'PADOVA'

        feedback.pushInfo(f'File TAF:    {taf_path}')
        feedback.pushInfo(f'GeoPackage:  {gpk_path}')
        feedback.pushInfo(f'Tabella:     {table_name}')
        feedback.pushInfo(f'CRS default: {_DEFAULT_CRS}')

        # ── Lettura file TAF ──────────────────────────────────────────────────
        feedback.pushInfo('\nLettura file TAF...')
        try:
            with open(taf_path, 'rb') as f:
                raw_lines = f.read().split(b'\r\n')
        except OSError as e:
            raise QgsProcessingException(f'Errore lettura file: {e}')

        records   = []
        n_invalid = 0
        for i, raw in enumerate(raw_lines):
            if feedback.isCanceled():
                raise QgsProcessingException('Operazione annullata.')
            if not raw.strip():
                continue
            try:
                line = raw.decode('latin-1')
            except UnicodeDecodeError:
                feedback.pushWarning(f'  Riga {i+1}: errore decodifica, saltata')
                n_invalid += 1
                continue
            rec = _parse_row(line)
            if rec is None:
                feedback.pushWarning(
                    f'  Riga {i+1}: lunghezza {len(raw)} (attesa {_ROW_LEN}), saltata')
                n_invalid += 1
                continue
            records.append(rec)

        total = len(records)
        feedback.pushInfo(f'Righe valide: {total}  |  Righe saltate: {n_invalid}')
        if total == 0:
            raise QgsProcessingException('Nessuna riga valida trovata nel file TAF.')

        # ── Costruzione GeoPackage ────────────────────────────────────────────
        feedback.pushInfo('\nCreazione GeoPackage...')

        fields = _build_fields()
        crs    = QgsCoordinateReferenceSystem(_DEFAULT_CRS)

        options              = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName   = 'GPKG'
        options.layerName    = table_name
        options.fileEncoding = 'UTF-8'

        writer = QgsVectorFileWriter.create(
            str(gpk_path),
            fields,
            QgsWkbTypes.Point,
            crs,
            QgsProject.instance().transformContext(),
            options
        )
        if writer.hasError() != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(
                f'Errore creazione GeoPackage: {writer.errorMessage()}')

        # ── Scrittura record ──────────────────────────────────────────────────
        feedback.pushInfo(f'Scrittura {total} record...')
        n_written      = 0
        n_no_geom      = 0
        n_with_allegato = 0

        for i, rec in enumerate(records):
            if feedback.isCanceled():
                raise QgsProcessingException('Operazione annullata.')
            if i % 500 == 0:
                feedback.setProgress(int(i / total * 100))

            feat = QgsFeature(fields)

            # Geometria: X = Coord_Est, Y = Coord_Nord
            e = rec['Coord_Est']
            n = rec['Coord_Nord']
            if e is not None and n is not None:
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(e, n)))
            else:
                # Coordinate mancanti: geometria nulla (punto non posizionato)
                feat.setGeometry(QgsGeometry())
                n_no_geom += 1

            feat['Codice_Comune']     = rec['Codice_Comune']
            feat['Codice_Sezione']    = rec['Codice_Sezione']    or None
            feat['Foglio']            = rec['Foglio']
            feat['Allegato']          = rec['Allegato']          or None
            feat['Fiduciale']         = rec['Fiduciale']
            feat['Particella']        = rec['Particella']        or None
            feat['Mono_Planimetrica'] = rec['Mono_Planimetrica'] or None
            feat['Coord_Nord']        = rec['Coord_Nord']
            feat['Coord_Est']         = rec['Coord_Est']
            feat['Attendibilita']     = rec['Attendibilita']
            feat['Foglio_Origine']    = rec['Foglio_Origine']
            feat['Allegato_Origine']  = rec['Allegato_Origine']  or None
            feat['Fiduciale_Origine'] = rec['Fiduciale_Origine']
            feat['Data_Aggiorn']      = rec['Data_Aggiorn']      or None
            feat['Causale_Aggiorn']   = rec['Causale_Aggiorn']   or None
            feat['Mono_Altimetrica']  = rec['Mono_Altimetrica']  or None
            feat['Attend_Altim']      = rec['Attend_Altim']
            feat['Quota']             = rec['Quota']
            feat['Codice_TAF']        = rec['Codice_TAF']
            # Campi monografia: tutti None all'importazione
            feat['Accesso']           = None
            feat['Piano_Paragone']    = None
            feat['Note']              = None
            feat['Origine']           = None
            feat['Istituito']         = None
            feat['Verificato']        = None
            feat['Annullato']         = None
            feat['GB_Nord']           = None
            feat['GB_Est']            = None
            feat['GB_Fuso']           = None
            feat['LAT']               = None
            feat['LON']               = None
            feat['H_Ellissoidica']    = None
            feat['UTM2000_Nord']      = None
            feat['UTM2000_Est']       = None
            feat['Foto']              = None

            writer.addFeature(feat)
            n_written += 1

            if rec['Allegato']:
                n_with_allegato += 1

        # Flush su disco
        del writer

        feedback.setProgress(100)
        feedback.pushInfo(f'\nRecord scritti:       {n_written}')
        if n_no_geom:
            feedback.pushWarning(f'Punti senza coordinate: {n_no_geom} (geometria nulla)')

        if n_with_allegato:
            feedback.pushWarning(
                f'\nATTENZIONE: {n_with_allegato} punti hanno Allegato foglio '
                f'(es. All=A/B...). Il Codice_TAF per questi punti e\' costruito '
                f'senza allegato e potrebbe non trovare la monografia sul sito AdE. '
                f'Verificare manualmente filtrando: "Allegato" IS NOT NULL.'
            )

        feedback.pushInfo(
            f'\nCRS assegnato: {_DEFAULT_CRS} — se il comune usa un sistema '
            f'diverso (es. EPSG:3004 per zona est, o Cassini-Soldner locale) '
            f'impostare il CRS corretto con tasto destro → Imposta CRS del layer.'
        )

        # ── Crea tabella _comuni con i Belfiore distinti ─────────────────────
        feedback.pushInfo('\nCreazione tabella comuni...')
        table_comuni = f'{table_name}_comuni'
        try:
            import sqlite3 as _sqlite3
            _conn = _sqlite3.connect(str(gpk_path))
            _c    = _conn.cursor()
            _c.execute(f'DROP TABLE IF EXISTS "{table_comuni}"')
            _c.execute(f"""
                CREATE TABLE "{table_comuni}" (
                    Belfiore TEXT PRIMARY KEY,
                    Comune   TEXT
                )""")
            _c.execute(f"""
                INSERT INTO "{table_comuni}" (Belfiore, Comune)
                SELECT DISTINCT Codice_Comune, ''
                FROM "{table_name}"
                ORDER BY Codice_Comune""")
            _c.execute(
                "INSERT OR REPLACE INTO gpkg_contents "
                "VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),NULL,NULL,NULL,NULL,NULL)",
                (table_comuni, 'attributes', table_comuni, 'Comuni distinti del TAF'))
            _conn.commit()
            n_comuni = _c.execute(
                f'SELECT COUNT(*) FROM "{table_comuni}"').fetchone()[0]
            _conn.close()
            feedback.pushInfo(f'  {table_comuni!r}: {n_comuni} comuni')
        except Exception as _e:
            feedback.pushWarning(f'  Tabella comuni non creata: {_e}')

        # ── Aggiunta al progetto ──────────────────────────────────────────────
        if add_layer:
            uri = f'{gpk_path}|layername={table_name}'
            lyr = QgsVectorLayer(uri, table_name, 'ogr')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
                feedback.pushInfo(f'\nLayer "{table_name}" aggiunto al progetto.')

                # ── Applica stile QML ─────────────────────────────────────────
                if apply_qml:
                    proj_path = QgsProject.instance().homePath()
                    if proj_path:
                        qml_path = Path(proj_path) / 'QML' / 'TAF.qml'
                        if qml_path.exists():
                            msg, ok = lyr.loadNamedStyle(str(qml_path))
                            if ok:
                                lyr.triggerRepaint()
                                feedback.pushInfo(f'  Stile QML applicato: {qml_path}')
                            else:
                                feedback.pushWarning(f'  Stile QML non applicato: {msg}')
                        else:
                            feedback.pushWarning(
                                f'  File QML non trovato: {qml_path}\n'
                                f'  Creare la cartella QML nella cartella del progetto '
                                f'e copiare TAF.qml.')
                    else:
                        feedback.pushWarning(
                            '  Stile QML non applicato: progetto non salvato.\n'
                            '  Salvare il progetto prima di importare.')
            else:
                feedback.pushWarning(f'Layer creato ma non caricabile: {uri}')

        feedback.pushInfo(f'\nDone → {gpk_path}')
        return {}

    # ── Metodi obbligatori ────────────────────────────────────────────────────

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading
