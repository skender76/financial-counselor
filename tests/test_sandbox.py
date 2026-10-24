"""Controlli statici del sandbox (senza Docker): la verifica vera è `sandbox/sandbox.sh verify`."""

import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'sandbox', 'sandbox.sh')
DOCKERFILE = os.path.join(ROOT, 'sandbox', 'Dockerfile')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


class SandboxStaticTest(unittest.TestCase):
    def test_sintassi_dello_script(self):
        result = subprocess.run(['bash', '-n', SCRIPT], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_immagine_senza_codice_del_progetto(self):
        copies = re.findall(r'^\s*(?:COPY|ADD)\s+(.+)$', read(DOCKERFILE), re.MULTILINE)
        self.assertEqual(copies, ['requirements.txt /tmp/requirements.txt'])

    def test_rete_disattivata_per_impostazione_predefinita(self):
        script = read(SCRIPT)
        self.assertIn('local net=none', script)
        self.assertIn('--cap-drop ALL', script)
        self.assertIn('--read-only', script)

    def test_non_monta_il_repository_reale(self):
        # L'unico volume è la cartella del sandbox, mai REPO_ROOT né la home.
        volumes = re.findall(r'-v\s+"([^"]+)"', read(SCRIPT))
        self.assertEqual(volumes, ['$SANDBOX_DIR:/work'])
        mounts = re.findall(r'--mount\s+"([^"]+)"', read(SCRIPT))
        self.assertEqual(mounts, ['$SANDBOX_DIR:w'])

    def test_il_clone_non_ha_remote(self):
        self.assertIn('remote remove origin', read(SCRIPT))


if __name__ == '__main__':
    unittest.main()
