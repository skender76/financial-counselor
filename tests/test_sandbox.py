"""Controlli statici del sandbox (senza Docker): la verifica vera è `sandbox/sandbox.sh verify`."""

import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'sandbox', 'sandbox.sh')
DOCKERFILE = os.path.join(ROOT, 'sandbox', 'Dockerfile')
GUARD = os.path.join(ROOT, 'sandbox', 'require-sandbox.sh')


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

    def test_git_funziona_con_la_proprieta_dei_mount(self):
        script = read(SCRIPT)
        self.assertIn('GIT_CONFIG_KEY_0=safe.directory', script)
        # Il controllo sul push deve fallire per mancanza di remote, non per altro.
        self.assertIn("No configured push destination", script)

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


class GuardTest(unittest.TestCase):
    """sandbox/require-sandbox.sh: blocca fuori dal sandbox, lascia passare dentro."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_guard(self, *args, script=GUARD, env=None):
        full = {k: v for k, v in os.environ.items() if k != 'DASHBOARD_ALLOW_HOST'}
        full['SANDBOX_DIR'] = os.path.join(self.tmp.name, 'sb')
        full.update(env or {})
        return subprocess.run(['bash', script, *args], capture_output=True, text=True,
                              env=full, timeout=30)

    def guard_with_marker(self, content):
        """Copia della guardia con il segno su un file di prova (il vero è /etc/dashboard-sandbox)."""
        marker = os.path.join(self.tmp.name, 'marker')
        if content is not None:
            with open(marker, 'w') as f:
                f.write(content)
        script = os.path.join(self.tmp.name, 'guard.sh')
        text = read(GUARD)
        self.assertIn('MARKER=/etc/dashboard-sandbox', text)
        with open(script, 'w') as f:
            f.write(text.replace('MARKER=/etc/dashboard-sandbox', 'MARKER=' + marker))
        return script

    def test_blocca_fuori_dal_sandbox(self):
        r = self.run_guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn('sandbox/sandbox.sh agent', r.stderr)

    def test_modalita_hook_esce_con_2(self):
        r = self.run_guard('--hook')
        self.assertEqual(r.returncode, 2)
        self.assertIn('Bloccato', r.stderr)

    def test_passa_dentro_il_sandbox(self):
        script = self.guard_with_marker('dashboard-sandbox-v1\n')
        for args in ((), ('--hook',)):
            r = self.run_guard(*args, script=script)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_segno_sbagliato_o_assente_blocca(self):
        for content in (None, 'altro', ''):
            r = self.run_guard(script=self.guard_with_marker(content))
            self.assertEqual(r.returncode, 1, content)

    def test_variabile_dambiente_da_sola_non_basta(self):
        r = self.run_guard(env={'DASHBOARD_SANDBOX': '1'})
        self.assertEqual(r.returncode, 1)

    def test_uscita_di_emergenza_registrata(self):
        log = os.path.join(self.tmp.name, 'sb', 'override.log')
        for _ in range(2):  # stessa sessione (stesso PPID): una sola riga
            r = self.run_guard('--hook', env={'DASHBOARD_ALLOW_HOST': '1'})
            self.assertEqual(r.returncode, 0, r.stderr)
        lines = read(log).strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ override ppid=\d+ cwd=')

    def test_uscita_di_emergenza_senza_registro_rifiuta(self):
        blocker = os.path.join(self.tmp.name, 'file')
        open(blocker, 'w').close()
        # SANDBOX_DIR sotto un file: il registro non si può scrivere.
        r = self.run_guard(env={'DASHBOARD_ALLOW_HOST': '1',
                                'SANDBOX_DIR': os.path.join(blocker, 'sb')})
        self.assertEqual(r.returncode, 1)
        self.assertIn('registrata', r.stderr)

    def test_altri_valori_non_sono_uscita_di_emergenza(self):
        for value in ('0', 'true', 'yes', ''):
            r = self.run_guard(env={'DASHBOARD_ALLOW_HOST': value})
            self.assertEqual(r.returncode, 1, value)

    def test_immagine_e_script_usano_lo_stesso_segno(self):
        self.assertIn('echo dashboard-sandbox-v1 > /etc/dashboard-sandbox', read(DOCKERFILE))
        self.assertIn('EXPECTED=dashboard-sandbox-v1', read(GUARD))
        self.assertIn('status)', read(SCRIPT))


if __name__ == '__main__':
    unittest.main()
