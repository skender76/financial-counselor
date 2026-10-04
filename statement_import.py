"""
Lettura degli estratti conto bancari (Revolut, BPER, Fineco) per la dashboard.

Questo modulo SOLO legge e normalizza: non scrive dati.json e non tocca saldi.
`parse_statement()` restituisce i movimenti in un formato comune e un riepilogo
con i controlli di coerenza; riconciliazione e categorizzazione vengono dopo.

Formati supportati (tipo "conto"):
- Revolut: CSV (virgola, UTF-8) con colonne Type, Started Date, Completed Date,
  Description, Amount, Fee, State...
- BPER: Excel .xls "Lista Movimenti" (date in italiano, colonne Entrate/Uscite
  separate, colonna Categoria della banca, riga finale "Totale")
- Fineco: Excel .xlsx "movements" (date vere, colonne Entrate/Uscite separate)

Tipo "carta" (movimenti della carta di credito): solo BPER, Excel .xls. Gli acquisti
diventano movimenti `carta_credito`; le righe "PAGAMENTO CON ADDEBITO SU VS C/C" sono
il regolamento mensile visto dal lato carta e vengono scartate (il regolamento è già
nell'estratto conto, riga "ADDEBITO CARTA CRED.").

Escluse dai movimenti ma restituite in `ignored`, perché non sono né spese né entrate:
- cambi valuta Revolut (tag `cambio_valuta`): il saldo sposta soldi tra tasche
- regolamenti carta dell'estratto conto BPER (tag `regolamento_carta`): altrimenti
  ogni acquisto con carta sarebbe contato due volte

Si importano solo i movimenti dal 1° gennaio 2026 (IMPORT_START_DATE).

Forma di ogni movimento restituito:
    {date: 'AAAA-MM-GG', direction: 'addebito'|'accredito', account, movement,
     amount (sempre > 0), description, details, tag, provisional}
`movement` usa gli stessi valori del registro Movimenti della dashboard. `tag` è
solo un suggerimento per i passi successivi (es. 'stipendio', 'mutuo',
'possibile_giroconto', 'regolamento_carta', 'cambio_valuta').

xlrd (.xls) e openpyxl (.xlsx) sono importati solo quando servono.
"""

import csv
import datetime
import io
import re

MAX_FILE_BYTES = 15 * 1024 * 1024

# Data minima di importazione: i movimenti precedenti non vengono né importati né
# usati per segnalare quelli registrati.
IMPORT_START_DATE = '2026-01-01'

ACCOUNTS = ('Revolut', 'BPER', 'Fineco')
KINDS = ('conto', 'carta')

# Estensione attesa del file per ogni conto.
EXPECTED_EXTENSION = {'Revolut': '.csv', 'BPER': '.xls', 'Fineco': '.xlsx'}

ITALIAN_MONTHS = {
    'gennaio': 1, 'febbraio': 2, 'marzo': 3, 'aprile': 4, 'maggio': 5, 'giugno': 6,
    'luglio': 7, 'agosto': 8, 'settembre': 9, 'ottobre': 10, 'novembre': 11, 'dicembre': 12,
}


class ImportError_(Exception):
    """Errore di lettura del file: il messaggio è in italiano, pronto per la pagina."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _collapse(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def _round(x):
    return round(x + 0.0, 2)


def _parse_it_number(text):
    """'33.374,25' -> 33374.25 (formato italiano, usato nelle righe di totale)."""
    s = str(text).replace('€', '').replace(' ', '').strip()
    if not s:
        return None
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def _to_number(value):
    """Cella numerica -> float, '' / None -> None."""
    if value is None or value == '' or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _parse_it_number(value)


def _parse_it_date(text):
    """'29 settembre 2026' -> '2026-09-29'."""
    m = re.match(r'^\s*(\d{1,2})\s+([a-zà]+)\s+(\d{4})\s*$', str(text).lower())
    if not m or m.group(2) not in ITALIAN_MONTHS:
        raise ImportError_('Data non riconosciuta: "%s".' % text)
    return '%04d-%02d-%02d' % (int(m.group(3)), ITALIAN_MONTHS[m.group(2)], int(m.group(1)))


def _movement(date, account, signed_amount, movement, description, details='', tag=None, provisional=False):
    return {
        'date': date,
        'direction': 'accredito' if signed_amount > 0 else 'addebito',
        'account': account,
        'movement': movement,
        'amount': _round(abs(signed_amount)),
        'description': description,
        'details': details,
        'tag': tag,
        'provisional': provisional,
    }


# ---------------------------------------------------------------- Revolut

REVOLUT_COLUMNS = ('Type', 'Started Date', 'Completed Date', 'Description', 'Amount', 'Fee', 'State')


def _parse_revolut(content):
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ImportError_('Il file non è un CSV Revolut valido (codifica non UTF-8).')
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REVOLUT_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ImportError_('Il file non sembra un estratto Revolut: mancano le colonne %s.' % ', '.join(missing))

    movements, skipped, warnings = [], {'annullati': 0, 'importo zero': 0}, []
    rows_read = 0
    for i, row in enumerate(reader, start=2):
        rows_read += 1
        state = (row['State'] or '').upper()
        if state == 'REVERTED':
            skipped['annullati'] += 1
            continue
        try:
            amount = float(row['Amount'])
            fee = float(row['Fee'] or 0)
        except ValueError:
            raise ImportError_('Riga %d: importo non valido ("%s").' % (i, row['Amount']))
        if (row.get('Currency') or 'EUR') != 'EUR':
            raise ImportError_('Riga %d: valuta %s non gestita (solo EUR).' % (i, row['Currency']))
        provisional = state != 'COMPLETED'
        date = (row['Completed Date'] or row['Started Date'] or '')[:10]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
            raise ImportError_('Riga %d: data non valida.' % i)
        desc = _collapse(row['Description'])
        kind = row['Type']

        if amount == 0 and fee == 0:
            skipped['importo zero'] += 1
            continue
        tag = None
        if kind == 'Card Payment':
            movement = 'carta_debito'
        elif kind == 'Card Refund':
            movement = 'accredito_diretto'
        elif kind == 'Exchange':
            movement, tag = 'bonifico', 'cambio_valuta'
        elif kind == 'Deposit':
            movement, tag = 'bonifico', 'possibile_giroconto'
        else:
            movement = 'bonifico'
        if amount != 0:
            movements.append(_movement(date, 'Revolut', amount, movement, desc, tag=tag, provisional=provisional))
        if fee != 0:
            movements.append(_movement(date, 'Revolut', -abs(fee), 'carta_debito',
                                       'Commissione: ' + desc, tag='spese_bancarie', provisional=provisional))
    if not movements:
        raise ImportError_('Nessun movimento trovato nel file Revolut.')
    pending = sum(1 for m in movements if m['provisional'])
    if pending:
        warnings.append('%d movimenti Revolut non sono ancora completati (in sospeso): sono marcati come provvisori.' % pending)
    return {
        'movements': movements,
        'rows_read': rows_read,
        'skipped': {k: v for k, v in skipped.items() if v},
        'checks': [],
        'warnings': warnings,
    }


# ---------------------------------------------------------------- BPER

# Categoria della banca -> (tipo movimento dashboard, tag)
BPER_CATEGORIES = {
    'ADDEBITO SDD/RID': ('rid', None),
    'PRELIEVO': ('ritiro_contanti', None),
    'BANCOMAT': ('carta_debito', None),
    'BONIFICO': ('bonifico', None),
    'STIPENDIO': ('bonifico', 'stipendio'),
    'RATA FINANZIAMENTO': ('rid', 'mutuo'),
    'ADDEBITO CARTA CRED.': ('rid', 'regolamento_carta'),
    'COMPETENZE': ('rid', 'spese_bancarie'),
    'SPESE': ('rid', 'spese_bancarie'),
    'COMMISSIONE': ('rid', 'spese_bancarie'),
    'IMPOSTE': ('rid', 'imposte'),
    'F24': ('bonifico', 'imposte'),
}


def _open_xls(content):
    try:
        import xlrd
    except ImportError:
        raise ImportError_('Per leggere i file .xls serve xlrd: .venv/bin/python -m pip install xlrd', status=500)
    try:
        return xlrd.open_workbook(file_contents=content)
    except Exception:
        raise ImportError_('Il file non è un Excel .xls valido.')


def _parse_bper(content):
    wb = _open_xls(content)
    sheet = wb.sheet_by_index(0)
    rows = [[c.value for c in sheet.row(r)] for r in range(sheet.nrows)]

    header_idx = next((i for i, r in enumerate(rows)
                       if len(r) > 7 and r[1] == 'Data operazione' and r[4] == 'Entrate'), None)
    if header_idx is None:
        raise ImportError_('Il file non sembra un estratto conto BPER: intestazione "Data operazione / Entrate / Uscite" non trovata.')

    declared = None
    for r in rows[:header_idx]:
        m = re.match(r'Numero Movimenti:\s*(\d+)', str(r[1]))
        if m:
            declared = int(m.group(1))
    totals = None
    movements, warnings, skipped = [], [], {}
    tot_in = tot_out = 0.0
    rows_read = 0
    for r in rows[header_idx + 1:]:
        if str(r[3]).strip() == 'Totale':
            totals = (_parse_it_number(r[4]), _parse_it_number(r[5]))
            continue
        # Righe senza una data (vuote, "Dati Aggiornati al ...") non sono movimenti.
        if not re.match(r'^\s*\d{1,2}\s+[a-zà]+\s+\d{4}\s*$', str(r[1]).lower()):
            continue
        rows_read += 1
        entrata, uscita = _to_number(r[4]), _to_number(r[5])
        signed = (entrata or 0.0) + (uscita or 0.0)  # le uscite sono già negative
        if signed == 0:
            skipped['importo zero'] = skipped.get('importo zero', 0) + 1
            continue
        if signed > 0:
            tot_in += signed
        else:
            tot_out += signed
        category = str(r[6]).strip()
        movement, tag = BPER_CATEGORIES.get(category, ('bonifico', None))
        if category and category not in BPER_CATEGORIES:
            warnings.append('Categoria BPER non riconosciuta: "%s" (trattata come bonifico).' % category)
        state = str(r[7]).strip()
        full = _collapse(r[3])
        movements.append(_movement(_parse_it_date(r[1]), 'BPER', signed, movement,
                                   full[:120], details=full, tag=tag,
                                   provisional=bool(state) and state != 'Contabilizzato'))

    checks = []
    if declared is not None:
        checks.append({'label': 'Numero movimenti dichiarato dal file', 'atteso': declared,
                       'letto': rows_read, 'ok': declared == rows_read})
    if totals and totals[0] is not None:
        checks.append({'label': 'Totale entrate', 'atteso': totals[0], 'letto': _round(tot_in),
                       'ok': abs(totals[0] - tot_in) < 0.01})
        checks.append({'label': 'Totale uscite', 'atteso': totals[1], 'letto': _round(tot_out),
                       'ok': abs(totals[1] - tot_out) < 0.01})
    if not movements:
        raise ImportError_('Nessun movimento trovato nel file BPER.')
    return {'movements': movements, 'rows_read': rows_read, 'skipped': skipped,
            'checks': checks, 'warnings': sorted(set(warnings))}


# ---------------------------------------------------------------- Fineco

def _fineco_tag(description):
    d = description.lower()
    if d == 'stipendio':
        return 'bonifico', 'stipendio'
    if any(k in d for k in ('acquisto fondi', 'compravendita titoli', 'rimborso quote fondo')):
        return 'bonifico', 'investimento'
    if any(k in d for k in ('bollo', 'canone', 'commissioni')):
        return 'rid', 'spese_bancarie'
    if any(k in d for k in ('f24', 'i24')):
        return 'bonifico', 'imposte'
    return 'bonifico', None


def _parse_fineco(content):
    try:
        import openpyxl
    except ImportError:
        raise ImportError_('Per leggere i file .xlsx serve openpyxl: .venv/bin/python -m pip install openpyxl', status=500)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception:
        raise ImportError_('Il file non è un Excel .xlsx valido.')
    rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]

    header_idx = next((i for i, r in enumerate(rows) if r and r[0] == 'Data_Operazione'), None)
    if header_idx is None:
        raise ImportError_('Il file non sembra un estratto conto Fineco: intestazione "Data_Operazione" non trovata.')
    cols = {name: i for i, name in enumerate(rows[header_idx]) if name}
    needed = ('Data_Operazione', 'Entrate', 'Uscite', 'Descrizione', 'Descrizione_Completa', 'Stato')
    missing = [c for c in needed if c not in cols]
    if missing:
        raise ImportError_('Estratto Fineco incompleto: mancano le colonne %s.' % ', '.join(missing))

    # Intestatario nelle due varianti (nome cognome / cognome nome), senza spazi:
    # nelle descrizioni lunghe Fineco spezza le parole, quindi si confronta senza spazi.
    holder_options = set()
    for r in rows[:header_idx]:
        m = re.match(r'Intestazione Conto Corrente:\s*(.+)', str(r[0] or ''))
        if m:
            parts = _collapse(m.group(1)).lower().split(' ')
            holder_options = {''.join(parts), ''.join(reversed(parts))}

    movements, warnings, skipped = [], [], {}
    rows_read = 0
    for r in rows[header_idx + 1:]:
        if not r or not r[cols['Data_Operazione']]:
            continue
        rows_read += 1
        entrata, uscita = _to_number(r[cols['Entrate']]), _to_number(r[cols['Uscite']])
        signed = (entrata or 0.0) + (uscita or 0.0)
        if signed == 0:
            skipped['importo zero'] = skipped.get('importo zero', 0) + 1
            continue
        date = r[cols['Data_Operazione']]
        if not isinstance(date, (datetime.datetime, datetime.date)):
            raise ImportError_('Data non riconosciuta nel file Fineco: "%s".' % date)
        desc = _collapse(r[cols['Descrizione']])
        details = _collapse(r[cols['Descrizione_Completa']])
        compact = details.lower().replace(' ', '')
        movement, tag = _fineco_tag(desc)
        if desc.lower().startswith('bonifico'):
            own = any(h in compact for h in holder_options)
            tag = 'possibile_giroconto' if own and 'giroconto' in compact else None
        state = str(r[cols['Stato']] or '').strip()
        movements.append(_movement(date.strftime('%Y-%m-%d'), 'Fineco', signed, movement, desc,
                                   details=details, tag=tag,
                                   provisional=bool(state) and state != 'Contabilizzato'))
    if not movements:
        raise ImportError_('Nessun movimento trovato nel file Fineco.')
    warnings.append('Il file Fineco non include i movimenti delle carte di credito.')
    return {'movements': movements, 'rows_read': rows_read, 'skipped': skipped,
            'checks': [], 'warnings': warnings}


# ---------------------------------------------------------------- BPER carta

CARD_SETTLEMENT_TEXT = 'PAGAMENTO CON ADDEBITO SU VS C/C'


def _parse_bper_carta(content):
    wb = _open_xls(content)
    sheet = wb.sheet_by_index(0)
    rows = [[c.value for c in sheet.row(r)] for r in range(sheet.nrows)]

    header_idx = next((i for i, r in enumerate(rows)
                       if len(r) > 5 and r[1] == 'Data operazione' and r[3] == 'Importo €'), None)
    if header_idx is None:
        raise ImportError_('Il file non sembra un estratto carta BPER: intestazione "Data operazione / Importo €" non trovata.')

    declared = None
    for r in rows[:header_idx]:
        m = re.match(r'Numero Movimenti Carta:\s*(\d+)', str(r[1]))
        if m:
            declared = int(m.group(1))

    movements, skipped = [], {}
    rows_read = 0
    for r in rows[header_idx + 1:]:
        if not re.match(r'^\s*\d{1,2}\s+[a-zà]+\s+\d{4}\s*$', str(r[1]).lower()):
            continue
        rows_read += 1
        desc = _collapse(r[2])
        amount = _to_number(r[3])
        if not amount:
            skipped['importo zero'] = skipped.get('importo zero', 0) + 1
            continue
        if desc.upper() == CARD_SETTLEMENT_TEXT:
            skipped['regolamenti carta'] = skipped.get('regolamenti carta', 0) + 1
            continue
        state = str(r[5]).strip()
        movements.append(_movement(_parse_it_date(r[1]), 'BPER', amount, 'carta_credito', desc,
                                   provisional=bool(state) and state != 'Contabilizzato'))

    checks = []
    if declared is not None:
        checks.append({'label': 'Numero movimenti carta dichiarato dal file', 'atteso': declared,
                       'letto': rows_read, 'ok': declared == rows_read})
    if not movements:
        raise ImportError_('Nessun movimento trovato nel file carta BPER.')
    return {'movements': movements, 'rows_read': rows_read, 'skipped': skipped,
            'checks': checks, 'warnings': []}


PARSERS = {'Revolut': _parse_revolut, 'BPER': _parse_bper, 'Fineco': _parse_fineco}


# Tag dei movimenti che non sono né spese né entrate: vanno in `ignored`.
IGNORED_TAGS = {'cambio_valuta': 'exchanges', 'regolamento_carta': 'card_settlements'}


def parse_statement(account, kind, filename, content, since=IMPORT_START_DATE):
    """Legge l'estratto e restituisce {movements, ignored, summary}. Solleva ImportError_."""
    if account not in ACCOUNTS:
        raise ImportError_('Conto non valido: "%s".' % account)
    if kind not in KINDS:
        raise ImportError_('Tipo di file non valido: "%s".' % kind)
    if kind == 'carta' and account != 'BPER':
        raise ImportError_('L\'estratto carta di credito è gestito solo per BPER.')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(since or '')):
        raise ImportError_('Data di inizio importazione non valida: "%s".' % since)
    if not content:
        raise ImportError_('Il file è vuoto.')
    if len(content) > MAX_FILE_BYTES:
        raise ImportError_('Il file supera i %d MB.' % (MAX_FILE_BYTES // (1024 * 1024)), status=413)
    expected = EXPECTED_EXTENSION[account]
    if not str(filename or '').lower().endswith(expected):
        raise ImportError_('Per %s è atteso un file %s (selezionato: "%s").' % (account, expected, filename))

    parser = _parse_bper_carta if kind == 'carta' else PARSERS[account]
    parsed = parser(content)
    all_movements = parsed['movements']
    skipped = dict(parsed['skipped'])
    for m in all_movements:
        m['source_kind'] = kind

    file_dates = [m['date'] for m in all_movements]
    ignored = {'exchanges': [], 'card_settlements': []}
    movements = []
    before_cap = 0
    for m in all_movements:
        if m['date'] < since:
            before_cap += 1
        elif m['tag'] in IGNORED_TAGS:
            ignored[IGNORED_TAGS[m['tag']]].append(m)
        else:
            movements.append(m)
    if before_cap:
        skipped['precedenti al %s' % since] = before_cap
    movements.sort(key=lambda m: m['date'], reverse=True)

    # Finestra in cui il file è la fonte di verità: dal più tardi tra primo
    # movimento del file e data minima, fino all'ultimo movimento del file.
    window_from, window_to = max(min(file_dates), since), max(file_dates)
    if window_from > window_to:
        raise ImportError_('Tutti i movimenti del file sono precedenti al %s: nulla da importare.' % since)
    warnings = list(parsed['warnings'])
    if not movements and not any(ignored.values()):
        raise ImportError_('Nessun movimento dal %s in poi nel file.' % since)

    def total(items, direction):
        return _round(sum(m['amount'] for m in items if m['direction'] == direction))

    summary = {
        'account': account,
        'kind': kind,
        'filename': filename,
        'since': since,
        'file_from': min(file_dates),
        'file_to': max(file_dates),
        'window_from': window_from,
        'window_to': window_to,
        'rows_read': parsed['rows_read'],
        'imported': len(movements),
        'skipped': skipped,
        'total_in': total(movements, 'accredito'),
        'total_out': total(movements, 'addebito'),
        'ignored_exchanges': {'count': len(ignored['exchanges']),
                              'net': _round(total(ignored['exchanges'], 'accredito') - total(ignored['exchanges'], 'addebito'))},
        'ignored_card_settlements': {'count': len(ignored['card_settlements']),
                                     'total': total(ignored['card_settlements'], 'addebito')},
        'checks': parsed['checks'],
        'warnings': warnings,
    }
    return {'movements': movements, 'ignored': ignored, 'summary': summary}
