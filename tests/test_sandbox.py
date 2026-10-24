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


class SandboxAgentTest(unittest.TestCase):
    """Il lancio dell'agente rifiuta gli usi sbagliati prima di toccare docker."""

    def agent(self, *args):
        return subprocess.run(['bash', SCRIPT, 'agent', *args], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=30)

    def test_env_accetta_solo_il_nome(self):
        r = self.agent('--env', 'CHIAVE=segreto', '--', 'true')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('accetta solo il nome', r.stderr)
        self.assertNotIn('segreto', r.stdout + r.stderr)

    def test_serve_un_comando(self):
        r = self.agent('--net')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('uso:', r.stderr)

    def test_serve_un_terminale(self):
        r = self.agent('--', 'true')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('terminale', r.stderr)

    def test_agente_nel_container_con_le_stesse_restrizioni(self):
        script = read(SCRIPT)
        # Un solo punto in cui parte il container, usato da comandi e agente.
        self.assertEqual(script.count('dk run'), 1)
        self.assertIn('SANDBOX_HOME=/work/agent-home container_exec', script)
        self.assertIn('AGENT_LABEL="dashboard-sandbox=agent"', script)

    def test_immagine_senza_agente_cablato(self):
        dockerfile = read(DOCKERFILE)
        self.assertIn('ARG AGENT_INSTALL', dockerfile)
        for name in ('claude', 'anthropic', 'openai', 'gemini'):
            self.assertNotIn(name, dockerfile.lower())


if __name__ == '__main__':
    unittest.main()
