# -*- coding: utf-8 -*-
"""
ImportaDIST.py  –  QGIS Processing Script
Licenza https://www.gnu.org/licenses/quick-guide-gplv3.html
V09 Giugno 2026 - Mauro Bettella + Claude AI
==========================================
Importa un file .DIS (Mutue Distanze tra punti Fiduciali - Agenzia delle Entrate)
in un GeoPackage con quattro tabelle:
  - <nome>           : LineString — righe con entrambi i PF trovati nel TAF
  - <nome>_onlypoint : Point      — righe con uno dei due PF mancanti nel TAF
  - <nome>_crs_diff  : LineString — righe con PF con coordinate in CRS differenti (GaussBoaga - CassiniSoldner)
  - <nome>_nogeom    : tabella    — righe con entrambi i PF mancanti nel TAF

Il GeoPackage viene salvato nella stessa cartella del file .DIS con lo stesso nome.
Il CRS delle geometrie viene impostato per default in EPSG:3003 Monte Mario / Italy zone 1.

Usa sqlite3 direttamente per inserimenti in batch — veloce e non blocca QGIS.

Installazione:
  Processing Toolbox → menu ⚙ → Add Script to Toolbox → seleziona questo file
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFile,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
    QgsVectorLayer,
    QgsCoordinateReferenceSystem,
    QgsProject,
)
import sqlite3
import struct
import math
from pathlib import Path


# ── Costanti ──────────────────────────────────────────────────────────────────
_ROW_LEN = 75
_BATCH   = 10000


# ── Lettura lookup TAF da sqlite3 (ignora filtri QGIS) ───────────────────────
def _parse_layer_source(source: str):
    """
    Estrae (gpkg_path, layername) dalla stringa source di un layer QGIS.
    Funziona con formati:
      path.gpkg|layername=nome|subset=...
      path.gpkg|layername=nome
    """
    # Rimuovi eventuale subset e altri parametri
    base = source.split('|')[0].strip()
    layername = None
    if '|layername=' in source:
        part = source.split('|layername=')[1]
        layername = part.split('|')[0].strip()
    return base, layername


def _carica_taf_lookup(taf_layer, feedback):
    """
    Legge il GeoPackage TAF direttamente con sqlite3,
    ignorando qualsiasi filtro attivo nel layer QGIS.
    Ritorna dizionario CodiceTAF → (x, y).
    """
    source = taf_layer.dataProvider().dataSourceUri()
    gpkg_path, layername = _parse_layer_source(source)

    if not Path(gpkg_path).exists():
        raise QgsProcessingException(
            f'File GeoPackage TAF non trovato: {gpkg_path}')

    if not layername:
        raise QgsProcessingException(
            f'Impossibile determinare il nome tabella dal layer TAF.\n'
            f'Source: {source}')

    feedback.pushInfo(f'  Leggo TAF da: {gpkg_path}')
    feedback.pushInfo(f'  Tabella: {layername}')

    def _xy_from_blob(blob):
        """Estrae X,Y da blob GeoPackage Point."""
        if not blob or len(blob) < 21:
            return None, None
        flags    = blob[3]
        env_type = (flags >> 1) & 0x07
        env_size = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env_type, 32)
        offset   = 8 + env_size + 5   # header + WKB byteorder+type
        if len(blob) < offset + 16:
            return None, None
        x = struct.unpack_from('<d', blob, offset)[0]
        y = struct.unpack_from('<d', blob, offset + 8)[0]
        if math.isnan(x) or math.isnan(y):
            return None, None
        return x, y

    conn = sqlite3.connect(gpkg_path)
    cur  = conn.cursor()

    # Scopre i nomi reali delle colonne (geometria e Codice_TAF)
    cur.execute(f'PRAGMA table_info("{layername}")')
    cols = [row[1] for row in cur.fetchall()]
    feedback.pushInfo(f'  Colonne trovate: {cols}')

    # Colonna geometria: prima colonna di tipo BLOB o con nome geom/geometry
    geom_col = None
    for c in cols:
        if c.lower() in ('geom', 'geometry', 'shape'):
            geom_col = c
            break
    if geom_col is None:
        # fallback: prima colonna dopo fid che non sia testo comune
        for c in cols:
            if c.lower() not in ('fid', 'ogc_fid'):
                cur.execute(f'SELECT typeof("{c}") FROM "{layername}" LIMIT 1')
                row = cur.fetchone()
                if row and row[0] == 'blob':
                    geom_col = c
                    break
    if geom_col is None:
        raise QgsProcessingException(
            f'Colonna geometria non trovata nella tabella {layername}.\n'
            f'Colonne disponibili: {cols}')

    # Colonna Codice_TAF: case-insensitive
    cod_col = None
    for c in cols:
        if c.lower() == 'codice_taf':
            cod_col = c
            break
    if cod_col is None:
        raise QgsProcessingException(
            f'Campo Codice_TAF non trovato nella tabella {layername}.\n'
            f'Colonne disponibili: {cols}')

    feedback.pushInfo(f'  Colonna geometria: {geom_col!r}')
    feedback.pushInfo(f'  Colonna codice:    {cod_col!r}')

    cur.execute(f'SELECT "{cod_col}", "{geom_col}" FROM "{layername}" '
                f'WHERE "{geom_col}" IS NOT NULL AND "{cod_col}" IS NOT NULL')

    lookup = {}
    for codice, blob in cur:
        x, y = _xy_from_blob(blob)
        if x is not None:
            lookup[str(codice)] = (x, y)
    conn.close()
    return lookup


# ── WKB GeoPackage LineString ─────────────────────────────────────────────────
def _gpkg_linestring(x1, y1, x2, y2, srs_id):
    """
    Produce blob GeoPackage per LineString 2D a 2 punti.
    Ritorna (blob, minx, maxx, miny, maxy) oppure None se coordinate invalide.
    """
    if any(math.isnan(v) or math.isinf(v) for v in [x1, y1, x2, y2]):
        return None
    minx, maxx = min(x1, x2), max(x1, x2)
    miny, maxy = min(y1, y2), max(y1, y2)
    blob = (b'GP\x00\x03'
            + struct.pack('<i',  srs_id)
            + struct.pack('<4d', minx, maxx, miny, maxy)
            + struct.pack('<bII d d d d', 1, 2, 2, x1, y1, x2, y2))
    return blob, minx, maxx, miny, maxy


# ── WKB GeoPackage Point ─────────────────────────────────────────────────────
def _gpkg_point(x, y, srs_id):
    """Produce blob GeoPackage per un Point 2D (no envelope)."""
    if math.isnan(x) or math.isnan(y):
        return None
    # flags=0x00: no envelope, little-endian
    # WKB Point: byteorder(1b) + type(1I) + x(d) + y(d)
    blob = (b'GP\x00\x00'
            + struct.pack('<i', srs_id)
            + struct.pack('<b', 1)       # WKB little-endian
            + struct.pack('<I', 1)       # WKBPoint
            + struct.pack('<2d', x, y))  # x, y
    return blob


# ── Rilevamento tipo coordinate ──────────────────────────────────────────────
def _is_cassini(x, y):
    """Stima se le coordinate sono Cassini-Soldner locale (|x|,|y| < 1.000.000)."""
    return abs(x) < 1_000_000 and abs(y) < 1_000_000


# ── Creazione struttura GeoPackage ────────────────────────────────────────────
def _crea_gpkg(gpkg_path, table_name, table_ng, table_op, table_cd, srs_id, srs_wkt):
    Path(gpkg_path).unlink(missing_ok=True)
    conn = sqlite3.connect(str(gpkg_path))
    c    = conn.cursor()

    c.executescript("""
PRAGMA application_id = 0x47504B47;
PRAGMA user_version   = 10300;
PRAGMA journal_mode   = WAL;
PRAGMA synchronous    = NORMAL;

CREATE TABLE gpkg_spatial_ref_sys (
    srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
    organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
    definition TEXT NOT NULL, description TEXT);

CREATE TABLE gpkg_contents (
    table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
    identifier TEXT, description TEXT DEFAULT '',
    last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    min_x REAL, min_y REAL, max_x REAL, max_y REAL, srs_id INTEGER);

CREATE TABLE gpkg_geometry_columns (
    table_name TEXT NOT NULL, column_name TEXT NOT NULL,
    geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
    z TINYINT NOT NULL, m TINYINT NOT NULL,
    CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));

CREATE TABLE gpkg_extensions (
    table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL,
    definition TEXT NOT NULL, scope TEXT NOT NULL,
    CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name));
""")

    # SRS
    c.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
              (f'CRS {srs_id}', srs_id, 'EPSG', srs_id, srs_wkt, ''))
    for row in [('Undefined Cartesian', -1, 'NONE', -1, 'undefined', ''),
                ('Undefined Geographic', 0, 'NONE', 0, 'undefined', '')]:
        try:
            c.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)", row)
        except sqlite3.IntegrityError:
            pass

    # Tabella con geometria + RTree
    c.executescript(f"""
CREATE TABLE "{table_name}" (
    fid        INTEGER PRIMARY KEY AUTOINCREMENT,
    geom       BLOB,
    CodiceTAF1 TEXT, CodiceTAF2 TEXT,
    Comune1    TEXT, Sezione1 TEXT, Foglio1 INTEGER,
    Allegato1  TEXT, Fiduciale1 INTEGER,
    Comune2    TEXT, Sezione2 TEXT, Foglio2 INTEGER,
    Allegato2  TEXT, Fiduciale2 INTEGER,
    Protocollo TEXT, Distanza REAL, SQM REAL);

CREATE VIRTUAL TABLE "rtree_{table_name}_geom"
    USING rtree(id, minx, maxx, miny, maxy);

INSERT INTO gpkg_extensions VALUES
    ('{table_name}','geom','gpkg_rtree_index',
     'http://www.geopackage.org/spec120/#extension_rtree_index','write-only');
""")

    c.execute("INSERT INTO gpkg_contents VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),NULL,NULL,NULL,NULL,?)",
              (table_name, 'features', table_name, '', srs_id))
    c.execute("INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
              (table_name, 'geom', 'LINESTRING', srs_id, 0, 0))

    # Tabella CRS diversi (LineString geometricamente approssimativa)
    c.executescript(f"""
CREATE TABLE "{table_cd}" (
    fid        INTEGER PRIMARY KEY AUTOINCREMENT,
    geom       BLOB,
    CodiceTAF1 TEXT, CodiceTAF2 TEXT,
    Comune1    TEXT, Sezione1 TEXT, Foglio1 INTEGER,
    Allegato1  TEXT, Fiduciale1 INTEGER,
    Comune2    TEXT, Sezione2 TEXT, Foglio2 INTEGER,
    Allegato2  TEXT, Fiduciale2 INTEGER,
    Protocollo TEXT, Distanza REAL, SQM REAL,
    Motivo     TEXT);

CREATE VIRTUAL TABLE "rtree_{table_cd}_geom"
    USING rtree(id, minx, maxx, miny, maxy);

INSERT INTO gpkg_extensions VALUES
    ('{table_cd}','geom','gpkg_rtree_index',
     'http://www.geopackage.org/spec120/#extension_rtree_index','write-only');
""")
    c.execute("INSERT INTO gpkg_contents VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),NULL,NULL,NULL,NULL,?)",
              (table_cd, 'features', table_cd, 'Linee con CRS diversi (geometria approssimativa)', srs_id))
    c.execute("INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
              (table_cd, 'geom', 'LINESTRING', srs_id, 0, 0))

    # Tabella Point per coppie coincidenti
    c.executescript(f"""
CREATE TABLE "{table_op}" (
    fid        INTEGER PRIMARY KEY AUTOINCREMENT,
    geom       BLOB,
    CodiceTAF1 TEXT, CodiceTAF2 TEXT,
    Comune1    TEXT, Sezione1 TEXT, Foglio1 INTEGER,
    Allegato1  TEXT, Fiduciale1 INTEGER,
    Comune2    TEXT, Sezione2 TEXT, Foglio2 INTEGER,
    Allegato2  TEXT, Fiduciale2 INTEGER,
    Protocollo TEXT, Distanza REAL, SQM REAL,
    Motivo     TEXT);
""")
    c.execute("INSERT INTO gpkg_contents VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),NULL,NULL,NULL,NULL,?)",
              (table_op, 'features', table_op, 'Coppie PF coincidenti', srs_id))
    c.execute("INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
              (table_op, 'geom', 'POINT', srs_id, 0, 0))

    c.execute(f"""CREATE TABLE "{table_ng}" (
        fid        INTEGER PRIMARY KEY AUTOINCREMENT,
        CodiceTAF1 TEXT, CodiceTAF2 TEXT,
        Comune1    TEXT, Sezione1 TEXT, Foglio1 INTEGER,
        Allegato1  TEXT, Fiduciale1 INTEGER,
        Comune2    TEXT, Sezione2 TEXT, Foglio2 INTEGER,
        Allegato2  TEXT, Fiduciale2 INTEGER,
        Protocollo TEXT, Distanza REAL, SQM REAL,
        Motivo     TEXT)""")

    c.execute("INSERT INTO gpkg_contents VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),NULL,NULL,NULL,NULL,?)",
              (table_ng, 'attributes', table_ng, '', -1))

    conn.commit()
    return conn


# ── Parsing riga .DIS ─────────────────────────────────────────────────────────
def _parse_dis(line):
    if len(line) < _ROW_LEN:
        return None

    def _i(s):
        v = s.strip()
        return int(v) if v.lstrip('-').isdigit() else None

    def _f(s):
        try:    return float(s.strip())
        except: return None

    def _cod(comune, sezione, foglio, allegato, fid):
        fog = foglio if foglio is not None else 0
        f   = fid    if fid    is not None else 0
        sez = sezione.strip() if sezione else ''
        al  = allegato.strip() if allegato and allegato.strip() else '0'
        return f"{comune}{sez}-{fog:03d}{al}-{f:02d}"

    c1  = line[0:4].strip()
    s1  = line[4:5].strip()
    f1  = _i(line[6:10])
    a1  = line[10:11].strip()
    id1 = _i(line[11:17])
    c2  = line[18:22].strip()
    s2  = line[22:23].strip()
    f2  = _i(line[24:28])
    a2  = line[28:29].strip()
    id2 = _i(line[29:35])

    if not c1 or not c2:
        return None

    return (
        _cod(c1, s1, f1, a1, id1),   # 0  CodiceTAF1
        _cod(c2, s2, f2, a2, id2),   # 1  CodiceTAF2
        c1, s1 or None, f1, a1 or None, id1,   # 2-6
        c2, s2 or None, f2, a2 or None, id2,   # 7-11
        line[36:46].strip() or None,            # 12 Protocollo
        _f(line[46:62]),                        # 13 Distanza
        _f(line[62:75]),                        # 14 SQM
    )


# ── Processing Algorithm ──────────────────────────────────────────────────────

class ImportaDIST(QgsProcessingAlgorithm):

    INPUT_DIS = 'INPUT_DIS'
    INPUT_TAF = 'INPUT_TAF'
    ADD_LAYER = 'ADD_LAYER'
    APPLY_QML = 'APPLY_QML'

    def tr(self, s):
        return QCoreApplication.translate('ImportaDIST', s)

    def createInstance(self):
        return ImportaDIST()

    def name(self):
        return 'importadist'

    def displayName(self):
        return self.tr('Importa file DIST in GeoPackage')

    def group(self):
        return self.tr('Catasto')

    def groupId(self):
        return 'catasto'

    def shortHelpString(self):
        return self.tr(
            'Importa un file .DIS (Mutue Distanze tra punti Fiduciali)\n'
            'in un GeoPackage con quattro tabelle:\n\n'
            '<table style="font-family:monospace;border-collapse:collapse;">'
            '<tr style="background:#dde;">'  
            '<th style="padding:2px 6px;">File</th>'
            '<th style="padding:2px 6px;">Geometria</th>'
            '<th style="padding:2px 6px;">Informazioni</th>'
            '</tr>'
            '<tr>'
            '<td style="padding:2px 6px;">(nomeDIS)</td>'
            '<td style="padding:2px 6px;">LineString</td>'
            '<td style="padding:2px 6px;">righe con entrambi i PF trovati nel TAF</td>'
            '</tr>'
            '<tr style="background:#eef;">'
            '<td style="padding:2px 6px;">(nomeDIS)_onlypoint</td>'
            '<td style="padding:2px 6px;">Point</td>'
            '<td style="padding:2px 6px;">righe con uno dei due PF mancanti nel TAF</td>'
            '</tr>'
            '<tr>'
            '<td style="padding:2px 6px;">(nomeDIS)_crs_diff</td>'
            '<td style="padding:2px 6px;">LineString</td>'
            '<td style="padding:2px 6px;">righe con PF con coordinate in CRS differenti (GaussBoaga - CassiniSoldner)</td>'
            '</tr>'
            '<tr style="background:#eef;">'
            '<td style="padding:2px 6px;">(nomeDIS)_nogeom</td>'
            '<td style="padding:2px 6px;">Tabella</td>'
            '<td style="padding:2px 6px;">righe con entrambi i PF mancanti nel TAF</td>'
            '</tr></table>\n\n'
            'Il GeoPackage viene salvato nella stessa cartella del .DIS.\n'
            'Il CRS delle geometrie viene impostato per default in EPSG:3003 Monte Mario / Italy zone 1.\n\n'
            'NOTA: eventuali filtri attivi sul layer TAF vengono ignorati\n'
            'per usare sempre tutti i punti disponibili.')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.INPUT_DIS,
            self.tr('File DIST (.dis)'),
            extension='dis'))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_TAF,
            self.tr('Layer TAF (punti fiduciali)'),
            [QgsProcessing.TypeVectorPoint]))

        self.addParameter(QgsProcessingParameterBoolean(
            self.ADD_LAYER,
            self.tr("Aggiungi i layer al progetto dopo l'importazione"),
            defaultValue=True))

        self.addParameter(QgsProcessingParameterBoolean(
            self.APPLY_QML,
            self.tr('Applica stile QML (QML/DIS.qml, DIS_onlypoint.qml, DIS_crs_diff.qml)'),
            defaultValue=True))

    def processAlgorithm(self, parameters, context, feedback):
        dis_path  = Path(self.parameterAsFile(parameters, self.INPUT_DIS, context))
        taf_layer = self.parameterAsVectorLayer(parameters, self.INPUT_TAF, context)
        add_layer = self.parameterAsBool(parameters, self.ADD_LAYER, context)
        apply_qml = (self.parameterAsBool(parameters, self.APPLY_QML, context)
                     if add_layer else False)

        if not dis_path.exists():
            raise QgsProcessingException(f'File non trovato: {dis_path}')

        gpkg_path  = dis_path.with_suffix('.gpkg')
        table_name = dis_path.stem
        table_ng   = f'{table_name}_nogeom'
        table_op   = f'{table_name}_onlypoint'
        table_cd   = f'{table_name}_crs_diff'

        feedback.pushInfo(f'File DIS:    {dis_path}')
        feedback.pushInfo(f'GeoPackage:  {gpkg_path}')
        feedback.pushInfo(f'Tabella:     {table_name}')
        feedback.pushInfo(f'Tabella NG:  {table_ng}')
        feedback.pushInfo(f'Tabella OP:  {table_op}')
        feedback.pushInfo(f'Tabella CD:  {table_cd}')

        # Segnala filtro attivo (verrà ignorato)
        filtro = taf_layer.subsetString()
        if filtro:
            feedback.pushWarning(
                f'\nATTENZIONE: il layer TAF ha un filtro attivo: {filtro}\n'
                f'→ Il filtro viene IGNORATO, vengono usati tutti i punti TAF.')

        # CRS fisso EPSG:3003 — l'utente imposta il CRS corretto dopo l'importazione
        srs_id  = 3003
        crs_tmp = QgsCoordinateReferenceSystem('EPSG:3003')
        srs_wkt = crs_tmp.toWkt()
        feedback.pushInfo('CRS: EPSG:3003 (default — modificare dopo importazione se necessario)')

        # Lookup TAF — lettura diretta sqlite3, ignora filtri
        feedback.pushInfo('\nCaricamento lookup TAF...')
        taf_lookup = _carica_taf_lookup(taf_layer, feedback)
        feedback.pushInfo(f'  {len(taf_lookup)} punti caricati.')

        if len(taf_lookup) == 0:
            raise QgsProcessingException(
                'Nessun punto TAF caricato. '
                'Verificare che il layer TAF abbia geometrie valide '
                'e il campo Codice_TAF valorizzato.')

        # Lettura file .DIS
        feedback.pushInfo('\nLettura file DIS...')
        try:
            with open(dis_path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            raise QgsProcessingException(f'Errore lettura: {e}')

        raw_lines = [l for l in raw.split(b'\r\n') if l.strip()]
        total     = len(raw_lines)
        feedback.pushInfo(f'  Righe: {total}')

        # Crea GeoPackage
        feedback.pushInfo('\nCreazione GeoPackage...')
        conn = _crea_gpkg(gpkg_path, table_name, table_ng, table_op, table_cd, srs_id, srs_wkt)
        cur  = conn.cursor()

        sql_geom = f"""INSERT INTO "{table_name}"
            (geom, CodiceTAF1, CodiceTAF2,
             Comune1, Sezione1, Foglio1, Allegato1, Fiduciale1,
             Comune2, Sezione2, Foglio2, Allegato2, Fiduciale2,
             Protocollo, Distanza, SQM)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

        sql_rtree = f"""INSERT INTO "rtree_{table_name}_geom"
            (id, minx, maxx, miny, maxy) VALUES (?,?,?,?,?)"""

        sql_cd = f"""INSERT INTO "{table_cd}"
            (geom, CodiceTAF1, CodiceTAF2,
             Comune1, Sezione1, Foglio1, Allegato1, Fiduciale1,
             Comune2, Sezione2, Foglio2, Allegato2, Fiduciale2,
             Protocollo, Distanza, SQM, Motivo)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

        sql_rtree_cd = f"""INSERT INTO "rtree_{table_cd}_geom"
            (id, minx, maxx, miny, maxy) VALUES (?,?,?,?,?)"""

        sql_op = f"""INSERT INTO "{table_op}"
            (geom, CodiceTAF1, CodiceTAF2,
             Comune1, Sezione1, Foglio1, Allegato1, Fiduciale1,
             Comune2, Sezione2, Foglio2, Allegato2, Fiduciale2,
             Protocollo, Distanza, SQM, Motivo)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

        sql_ng = f"""INSERT INTO "{table_ng}"
            (CodiceTAF1, CodiceTAF2,
             Comune1, Sezione1, Foglio1, Allegato1, Fiduciale1,
             Comune2, Sezione2, Foglio2, Allegato2, Fiduciale2,
             Protocollo, Distanza, SQM, Motivo)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

        # Elaborazione
        feedback.pushInfo(f'\nElaborazione {total} righe...')
        batch_geom     = []
        batch_rtree    = []
        batch_ng       = []
        batch_op       = []
        batch_cd       = []
        batch_rtree_cd = []
        n_geom = n_nogeom = n_invalid = n_onlypoint = n_crsdiff = 0
        fid_counter    = 1
        fid_counter_cd = 1   # autoincrement manuale per RTree

        for i, raw_line in enumerate(raw_lines):
            if feedback.isCanceled():
                conn.close()
                raise QgsProcessingException('Operazione annullata.')

            try:
                line = raw_line.decode('latin-1').rstrip('\r\n')
            except UnicodeDecodeError:
                n_invalid += 1
                continue

            rec = _parse_dis(line)
            if rec is None:
                n_invalid += 1
                continue

            cod1, cod2 = rec[0], rec[1]
            pt1 = taf_lookup.get(cod1)
            pt2 = taf_lookup.get(cod2)

            if pt1 and pt2:
                if pt1 == pt2:
                    # Punti coincidenti → _onlypoint
                    blob = _gpkg_point(pt1[0], pt1[1], srs_id)
                    if blob:
                        batch_op.append((blob,) + rec + ('PF coincidenti',))
                        n_onlypoint += 1
                    else:
                        batch_ng.append(rec + ('Coordinate non valide',))
                        n_nogeom += 1
                else:
                    # Controlla se i due punti hanno CRS diverso
                    crs1_cassini = _is_cassini(pt1[0], pt1[1])
                    crs2_cassini = _is_cassini(pt2[0], pt2[1])
                    geom_result = _gpkg_linestring(
                        pt1[0], pt1[1], pt2[0], pt2[1], srs_id)
                    if geom_result:
                        blob, minx, maxx, miny, maxy = geom_result
                        if crs1_cassini != crs2_cassini:
                            motivo = ('PF1 Cassini, PF2 Gauss-Boaga'
                                      if crs1_cassini else
                                      'PF1 Gauss-Boaga, PF2 Cassini')
                            batch_cd.append((blob,) + rec + (motivo,))
                            batch_rtree_cd.append((fid_counter_cd, minx, maxx, miny, maxy))
                            fid_counter_cd += 1
                            n_crsdiff += 1
                        else:
                            batch_geom.append((blob,) + rec)
                            batch_rtree.append((fid_counter, minx, maxx, miny, maxy))
                            fid_counter += 1
                            n_geom += 1
                    else:
                        batch_ng.append(rec + ('Coordinate non valide',))
                        n_nogeom += 1
            elif pt1 or pt2:
                # Solo uno dei due trovato → _onlypoint
                if pt1:
                    px, py, motivo = pt1[0], pt1[1], f'Solo PF1 trovato: {cod1}'
                else:
                    px, py, motivo = pt2[0], pt2[1], f'Solo PF2 trovato: {cod2}'
                blob = _gpkg_point(px, py, srs_id)
                if blob:
                    batch_op.append((blob,) + rec + (motivo,))
                    n_onlypoint += 1
                else:
                    batch_ng.append(rec + ('Coordinate non valide',))
                    n_nogeom += 1
            else:
                # Nessuno trovato → _nogeom
                batch_ng.append(rec + ('PF1 e PF2 non trovati',))
                n_nogeom += 1

            # Commit batch
            if len(batch_geom) >= _BATCH:
                cur.executemany(sql_geom,  batch_geom)
                cur.executemany(sql_rtree, batch_rtree)
                conn.commit()
                batch_geom  = []
                batch_rtree = []

            if len(batch_ng) >= _BATCH:
                cur.executemany(sql_ng, batch_ng)
                conn.commit()
                batch_ng = []

            if len(batch_op) >= _BATCH:
                cur.executemany(sql_op, batch_op)
                conn.commit()
                batch_op = []

            if len(batch_cd) >= _BATCH:
                cur.executemany(sql_cd,       batch_cd)
                cur.executemany(sql_rtree_cd, batch_rtree_cd)
                conn.commit()
                batch_cd = []; batch_rtree_cd = []

            if (i + 1) % 10000 == 0:
                feedback.setProgress(int((i + 1) / total * 95))
                feedback.pushInfo(
                    f'  {i+1:>7}/{total}  '
                    f'geom={n_geom}  nogeom={n_nogeom}')

        # Flush finali
        if batch_geom:
            cur.executemany(sql_geom,  batch_geom)
            cur.executemany(sql_rtree, batch_rtree)
        if batch_ng:
            cur.executemany(sql_ng, batch_ng)
        if batch_op:
            cur.executemany(sql_op, batch_op)
        if batch_cd:
            cur.executemany(sql_cd,       batch_cd)
            cur.executemany(sql_rtree_cd, batch_rtree_cd)
        conn.commit()
        conn.close()

        feedback.setProgress(100)
        size_mb = gpkg_path.stat().st_size // 1024 // 1024
        feedback.pushInfo(f'\nRecord con geometria:      {n_geom}')
        feedback.pushInfo(f'Record CRS diversi:        {n_crsdiff}')
        feedback.pushInfo(f'Record punti coincidenti:  {n_onlypoint}')
        feedback.pushInfo(f'Record senza geometria:    {n_nogeom}')
        feedback.pushInfo(f'Righe non valide:        {n_invalid}')
        feedback.pushInfo(f'Dimensione GeoPackage:   {size_mb} MB')

        # Aggiunta al progetto
        if add_layer:
            # Mappa layer → nome QML corrispondente
            qml_map = {
                table_name: 'DIS.qml',
                table_cd:   'DIS_crs_diff.qml',
                table_op:   'DIS_onlypoint.qml',
                table_ng:   None,   # nessun QML per _nogeom
            }
            proj_path = QgsProject.instance().homePath() if apply_qml else None

            for ln in [table_name, table_cd, table_op, table_ng]:
                uri = f'{gpkg_path}|layername={ln}'
                lyr = QgsVectorLayer(uri, ln, 'ogr')
                if lyr.isValid():
                    QgsProject.instance().addMapLayer(lyr)
                    feedback.pushInfo(f'Layer "{ln}" aggiunto al progetto.')

                    # Applica QML se disponibile
                    if apply_qml and proj_path:
                        qml_name = qml_map.get(ln)
                        if qml_name:
                            qml_path = Path(proj_path) / 'QML' / qml_name
                            if qml_path.exists():
                                msg, ok = lyr.loadNamedStyle(str(qml_path))
                                if ok:
                                    lyr.triggerRepaint()
                                    feedback.pushInfo(f'  Stile QML applicato: {qml_name}')
                                else:
                                    feedback.pushWarning(f'  Stile QML non applicato: {msg}')
                            else:
                                feedback.pushWarning(
                                    f'  File QML non trovato: {qml_path}')
                    elif apply_qml and not proj_path:
                        feedback.pushWarning(
                            '  Stile QML non applicato: progetto non salvato.')
                else:
                    feedback.pushWarning(f'Layer "{ln}" non caricabile: {uri}')

        # ── Crea tabella _comuni (unione Comune1 + Comune2) ──────────────────
        feedback.pushInfo('\nCreazione tabella comuni...')
        table_comuni = f'{table_name}_comuni'
        try:
            import sqlite3 as _sqlite3
            _conn = _sqlite3.connect(str(gpkg_path))
            _c    = _conn.cursor()
            _c.execute(f'DROP TABLE IF EXISTS "{table_comuni}"')
            _c.execute(f"""
                CREATE TABLE "{table_comuni}" (
                    Belfiore TEXT PRIMARY KEY,
                    Comune   TEXT
                )""")
            _c.execute(f"""
                INSERT INTO "{table_comuni}" (Belfiore, Comune)
                SELECT DISTINCT cod, '' FROM (
                    SELECT Comune1 AS cod FROM "{table_name}" WHERE Comune1 IS NOT NULL
                    UNION
                    SELECT Comune2 AS cod FROM "{table_name}" WHERE Comune2 IS NOT NULL
                ) ORDER BY cod""")
            _c.execute(
                "INSERT OR REPLACE INTO gpkg_contents "
                "VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),NULL,NULL,NULL,NULL,NULL)",
                (table_comuni, 'attributes', table_comuni,
                 'Comuni distinti dalle distanze misurate'))
            _conn.commit()
            n_comuni = _c.execute(
                f'SELECT COUNT(*) FROM "{table_comuni}"').fetchone()[0]
            _conn.close()
            feedback.pushInfo(f'  {table_comuni!r}: {n_comuni} comuni')
        except Exception as _e:
            feedback.pushWarning(f'  Tabella comuni non creata: {_e}')

        feedback.pushInfo(f'\nDone → {gpkg_path}')
        return {}
