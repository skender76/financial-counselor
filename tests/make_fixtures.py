"""Genera le fixture sintetiche di tests/fixtures (nessun dato reale).

Serve solo quando si vogliono rigenerare i file: i test usano quelli già
committati. Richiede xlwt (solo qui, non per eseguire i test):

    .venv/bin/python -m pip install --target /percorso/xlwt-lib xlwt
    PYTHONPATH=/percorso/xlwt-lib .venv/bin/python tests/make_fixtures.py
"""

import csv
import datetime
import os

import openpyxl
import xlwt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def revolut():
    cols = ['Type', 'Product', 'Started Date', 'Completed Date', 'Description',
            'Amount', 'Fee', 'Currency', 'State', 'Balance']
    rows = [
        ['Card Payment', 'Current', '2026-02-03 10:00:00', '2026-02-03 10:05:00', 'Supermercato Test', '-25.50', '0.00', 'EUR', 'COMPLETED', '100.00'],
        ['Card Payment', 'Current', '2025-12-30 09:00:00', '2025-12-30 09:01:00', 'Vecchio acquisto', '-10.00', '0.00', 'EUR', 'COMPLETED', '125.50'],
        ['Exchange', 'Current', '2026-02-04 11:00:00', '2026-02-04 11:00:01', 'Exchange EUR to USD', '-100.00', '0.00', 'EUR', 'COMPLETED', '0.00'],
        ['Deposit', 'Current', '2026-02-05 12:00:00', '2026-02-05 12:00:30', 'Top-up da conto', '200.00', '0.00', 'EUR', 'COMPLETED', '200.00'],
        ['Transfer', 'Current', '2026-02-07 13:00:00', '2026-02-07 13:01:00', 'Bonifico a Tizio', '-40.00', '1.00', 'EUR', 'COMPLETED', '159.00'],
        ['Card Payment', 'Current', '2026-02-09 14:00:00', '2026-02-09 14:00:10', 'Acquisto annullato', '-9.00', '0.00', 'EUR', 'REVERTED', '159.00'],
        ['Card Payment', 'Current', '2026-02-08 15:00:00', '', 'Libreria in sospeso', '-7.00', '0.00', 'EUR', 'PENDING', '152.00'],
    ]
    with open(os.path.join(OUT, 'revolut.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)


def _bper_sheet(path, pre, header, body, totals=None):
    wb = xlwt.Workbook()
    ws = wb.add_sheet('Movimenti')
    r = 0
    for line in pre:
        ws.write(r, 1, line)
        r += 1
    for c, value in enumerate(header):
        ws.write(r, c, value)
    r += 1
    for row in body:
        for c, value in enumerate(row):
            if value is not None:
                ws.write(r, c, value)
        r += 1
    if totals:
        ws.write(r, 3, 'Totale')
        ws.write(r, 4, totals[0])
        ws.write(r, 5, totals[1])
    wb.save(path)


def bper_conto(name='bper_conto.xls', total_in='1.500,00', total_out='-800,00'):
    header = ['', 'Data operazione', 'Data valuta', 'Descrizione', 'Entrate', 'Uscite', 'Categoria', 'Stato']
    body = [
        ['', '5 febbraio 2026', '5 febbraio 2026', 'Accredito stipendio Azienda Test', 1500.0, None, 'STIPENDIO', 'Contabilizzato'],
        ['', '10 febbraio 2026', '10 febbraio 2026', 'Rata finanziamento   casa', None, -600.0, 'RATA FINANZIAMENTO', 'Contabilizzato'],
        ['', '12 febbraio 2026', '12 febbraio 2026', 'Addebito carta di credito', None, -120.0, 'ADDEBITO CARTA CRED.', 'Contabilizzato'],
        ['', '15 febbraio 2026', '15 febbraio 2026', 'Pagamento Bancomat negozio', None, -30.0, 'BANCOMAT', 'Contabilizzato'],
        ['', '20 dicembre 2025', '20 dicembre 2025', 'Prelievo sportello', None, -50.0, 'PRELIEVO', 'Contabilizzato'],
    ]
    _bper_sheet(os.path.join(OUT, name),
                ['Numero Movimenti: 5'], header, body, (total_in, total_out))


def bper_carta():
    header = ['', 'Data operazione', 'Descrizione', 'Importo €', '', 'Stato']
    body = [
        ['', '3 febbraio 2026', 'NEGOZIO TEST MILANO', -45.90, None, 'Contabilizzato'],
        ['', '8 febbraio 2026', 'ONLINE SHOP TEST', -12.0, None, 'Contabilizzato'],
        ['', '12 febbraio 2026', 'PAGAMENTO CON ADDEBITO SU VS C/C', 120.0, None, 'Contabilizzato'],
    ]
    _bper_sheet(os.path.join(OUT, 'bper_carta.xls'), ['Numero Movimenti Carta: 3'], header, body)


def fineco():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Intestazione Conto Corrente: Mario Rossi'])
    ws.append([])
    ws.append(['Data_Operazione', 'Data_Valuta', 'Entrate', 'Uscite', 'Descrizione', 'Descrizione_Completa', 'Stato'])
    d = datetime.datetime
    ws.append([d(2026, 3, 1), d(2026, 3, 1), 2000.0, None, 'Stipendio', 'Stipendio Azienda Test', 'Contabilizzato'])
    ws.append([d(2026, 3, 3), d(2026, 3, 3), None, -300.0, 'Bonifico', 'Bonifico a favore di Rossi Mario Giroconto', 'Contabilizzato'])
    ws.append([d(2026, 3, 5), d(2026, 3, 5), None, -2.0, 'Bollo', 'Imposta di bollo', 'Contabilizzato'])
    ws.append([d(2026, 3, 6), d(2026, 3, 6), None, -500.0, 'Acquisto fondi', 'Acquisto fondo Test', 'Contabilizzato'])
    ws.append([d(2026, 3, 7), d(2026, 3, 7), None, None, 'Movimento nullo', 'Importo zero', 'Contabilizzato'])
    wb.save(os.path.join(OUT, 'fineco.xlsx'))


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    revolut()
    bper_conto()
    bper_conto('bper_conto_totali_errati.xls', total_in='1.400,00', total_out='-800,00')
    bper_carta()
    fineco()
    print('Fixture scritte in', OUT)
