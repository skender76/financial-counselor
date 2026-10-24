"""Test di statement_import.py con fixture sintetiche (tests/fixtures)."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import statement_import as si  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def parse(account, kind, filename, **kw):
    with open(os.path.join(FIXTURES, filename), 'rb') as f:
        return si.parse_statement(account, kind, filename, f.read(), **kw)


def brief(result):
    """(data, direzione, importo, movimento, tag, provvisorio) di ogni movimento."""
    return [(m['date'], m['direction'], m['amount'], m['movement'], m['tag'], m['provisional'])
            for m in result['movements']]


class RevolutTest(unittest.TestCase):
    def test_movimenti(self):
        r = parse('Revolut', 'conto', 'revolut.csv')
        self.assertEqual(brief(r), [
            ('2026-02-08', 'addebito', 7.0, 'carta_debito', None, True),
            ('2026-02-07', 'addebito', 40.0, 'bonifico', None, False),
            ('2026-02-07', 'addebito', 1.0, 'carta_debito', 'spese_bancarie', False),
            ('2026-02-05', 'accredito', 200.0, 'bonifico', 'possibile_giroconto', False),
            ('2026-02-03', 'addebito', 25.5, 'carta_debito', None, False),
        ])

    def test_cambio_valuta_ignorato_e_scarti(self):
        r = parse('Revolut', 'conto', 'revolut.csv')
        self.assertEqual(len(r['ignored']['exchanges']), 1)
        self.assertEqual(r['summary']['ignored_exchanges'], {'count': 1, 'net': -100.0})
        self.assertEqual(r['summary']['skipped'], {'annullati': 1, 'precedenti al 2026-01-01': 1})
        self.assertEqual(r['summary']['imported'], 5)
        self.assertTrue(r['summary']['warnings'])  # movimento in sospeso

    def test_data_di_inizio(self):
        r = parse('Revolut', 'conto', 'revolut.csv', since='2026-02-06')
        self.assertEqual([m['date'] for m in r['movements']], ['2026-02-08', '2026-02-07', '2026-02-07'])


class BperContoTest(unittest.TestCase):
    def test_movimenti_e_tag(self):
        r = parse('BPER', 'conto', 'bper_conto.xls')
        self.assertEqual(brief(r), [
            ('2026-02-15', 'addebito', 30.0, 'carta_debito', None, False),
            ('2026-02-10', 'addebito', 600.0, 'rid', 'mutuo', False),
            ('2026-02-05', 'accredito', 1500.0, 'bonifico', 'stipendio', False),
        ])

    def test_regolamento_carta_ignorato(self):
        r = parse('BPER', 'conto', 'bper_conto.xls')
        self.assertEqual(len(r['ignored']['card_settlements']), 1)
        self.assertEqual(r['summary']['ignored_card_settlements'], {'count': 1, 'total': 120.0})
        self.assertEqual(r['summary']['skipped'], {'precedenti al 2026-01-01': 1})

    def test_controlli_incrociati_ok(self):
        checks = parse('BPER', 'conto', 'bper_conto.xls')['summary']['checks']
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(c['ok'] for c in checks), checks)

    def test_totali_alterati(self):
        checks = parse('BPER', 'conto', 'bper_conto_totali_errati.xls')['summary']['checks']
        failed = [c['label'] for c in checks if not c['ok']]
        self.assertEqual(failed, ['Totale entrate'])

    def test_finestra_di_verita(self):
        s = parse('BPER', 'conto', 'bper_conto.xls')['summary']
        self.assertEqual((s['window_from'], s['window_to']), ('2026-01-01', '2026-02-15'))
        self.assertEqual((s['file_from'], s['file_to']), ('2025-12-20', '2026-02-15'))


class BperCartaTest(unittest.TestCase):
    def test_acquisti_senza_regolamento(self):
        r = parse('BPER', 'carta', 'bper_carta.xls')
        self.assertEqual(brief(r), [
            ('2026-02-08', 'addebito', 12.0, 'carta_credito', None, False),
            ('2026-02-03', 'addebito', 45.9, 'carta_credito', None, False),
        ])
        self.assertEqual(r['summary']['skipped'], {'regolamenti carta': 1})
        self.assertTrue(all(c['ok'] for c in r['summary']['checks']))
        self.assertTrue(all(m['source_kind'] == 'carta' for m in r['movements']))


class FinecoTest(unittest.TestCase):
    def test_movimenti_e_tag(self):
        r = parse('Fineco', 'conto', 'fineco.xlsx')
        self.assertEqual(brief(r), [
            ('2026-03-06', 'addebito', 500.0, 'bonifico', 'investimento', False),
            ('2026-03-05', 'addebito', 2.0, 'rid', 'spese_bancarie', False),
            ('2026-03-03', 'addebito', 300.0, 'bonifico', 'possibile_giroconto', False),
            ('2026-03-01', 'accredito', 2000.0, 'bonifico', 'stipendio', False),
        ])
        self.assertEqual(r['summary']['skipped'], {'importo zero': 1})


class ErroriTest(unittest.TestCase):
    def read(self, filename):
        with open(os.path.join(FIXTURES, filename), 'rb') as f:
            return f.read()

    def assertRifiutato(self, *args, **kw):
        with self.assertRaises(si.ImportError_):
            si.parse_statement(*args, **kw)

    def test_parametri_non_validi(self):
        csv_bytes = self.read('revolut.csv')
        self.assertRifiutato('Unicredit', 'conto', 'revolut.csv', csv_bytes)
        self.assertRifiutato('Revolut', 'altro', 'revolut.csv', csv_bytes)
        self.assertRifiutato('Revolut', 'carta', 'revolut.csv', csv_bytes)
        self.assertRifiutato('Revolut', 'conto', 'revolut.csv', csv_bytes, since='ieri')

    def test_file_vuoto_o_estensione_sbagliata(self):
        self.assertRifiutato('Revolut', 'conto', 'revolut.csv', b'')
        self.assertRifiutato('BPER', 'conto', 'revolut.csv', self.read('revolut.csv'))

    def test_contenuto_non_riconosciuto(self):
        self.assertRifiutato('Revolut', 'conto', 'x.csv', b'a,b\n1,2\n')
        self.assertRifiutato('BPER', 'conto', 'x.xls', b'non e un excel')
        self.assertRifiutato('Fineco', 'conto', 'x.xlsx', b'non e un excel')
        # Un file Fineco dato come BPER: formato diverso.
        self.assertRifiutato('BPER', 'conto', 'x.xls', self.read('fineco.xlsx'))

    def test_tutti_i_movimenti_precedenti_alla_data(self):
        self.assertRifiutato('Revolut', 'conto', 'revolut.csv', self.read('revolut.csv'),
                             since='2030-01-01')

    def test_file_troppo_grande(self):
        with self.assertRaises(si.ImportError_) as ctx:
            si.parse_statement('Revolut', 'conto', 'revolut.csv', b'x' * (si.MAX_FILE_BYTES + 1))
        self.assertEqual(ctx.exception.status, 413)


if __name__ == '__main__':
    unittest.main()
