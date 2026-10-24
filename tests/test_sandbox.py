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
PROXY_DOCKERFILE = os.path.join(ROOT, 'sandbox', 'proxy', 'Dockerfile')
PROXY_CONF = os.path.join(ROOT, 'sandbox', 'proxy', 'tinyproxy.conf')
ALLOWLIST = os.path.join(ROOT, 'sandbox', 'allowlist.txt')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def extract_function(script_text, name):
    m = re.search(r'^' + re.escape(name) + r'\(\) \{.*?\n\}\n', script_text, re.M | re.S)
    assert m, f'funzione {name} non trovata'
    return m.group(0)


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
        # Il container principale monta solo la cartella del sandbox; il
        # proxy di sessione (ITEM-005) monta solo il filtro generato.
        # Mai REPO_ROOT né la home, in nessuno dei due.
        volumes = re.findall(r'-v\s+"([^"]+)"', read(SCRIPT))
        self.assertEqual(sorted(volumes),
                          sorted(['$SANDBOX_DIR:/work', '$filter_file:/etc/tinyproxy/filter:ro']))
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
        # Un punto per il container principale (container_exec) e uno per il
        # proxy di sessione (start_egress), usati sia da comandi che da agente.
        self.assertEqual(script.count('dk run'), 2)
        self.assertEqual(script.count('dk run -d'), 1)
        self.assertIn('SANDBOX_HOME=/work/agent-home container_exec', script)
        self.assertIn('AGENT_LABEL="dashboard-sandbox=agent"', script)

    def test_immagine_senza_agente_cablato(self):
        dockerfile = read(DOCKERFILE)
        self.assertIn('ARG AGENT_INSTALL', dockerfile)
        for name in ('claude', 'anthropic', 'openai', 'gemini'):
            self.assertNotIn(name, dockerfile.lower())


class EgressAllowlistTest(unittest.TestCase):
    """ITEM-005: allowlist di rete in uscita, senza Docker (static + funzione pura)."""

    def agent(self, *args):
        return subprocess.run(['bash', SCRIPT, 'agent', *args], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=30)

    def run_cmd(self, *args):
        return subprocess.run(['bash', SCRIPT, 'run', *args], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=30)

    def test_allowlist_esiste_con_api_anthropic(self):
        text = read(ALLOWLIST)
        hosts = [l.split('#', 1)[0].strip() for l in text.splitlines()]
        hosts = [h for h in hosts if h]
        self.assertIn('api.anthropic.com', hosts)
        for h in hosts:
            self.assertRegex(h, r'^[A-Za-z0-9.-]+$', f'riga malformata: {h!r}')

    def test_proxy_non_contiene_codice_del_progetto(self):
        copies = re.findall(r'^\s*(?:COPY|ADD)\s+(.+)$', read(PROXY_DOCKERFILE), re.MULTILINE)
        self.assertEqual(copies, ['tinyproxy.conf /etc/tinyproxy/tinyproxy.conf'])

    def test_proxy_nega_per_default(self):
        conf = read(PROXY_CONF)
        self.assertIn('FilterDefaultDeny Yes', conf)
        self.assertIn('/etc/tinyproxy/filter', conf)

    def test_net_non_usa_piu_bridge_diretto(self):
        script = read(SCRIPT)
        self.assertIn('net=egress', script)
        # "--network bridge" resta solo per collegare il proxy a Internet,
        # non per il container principale (che ora va sulla rete interna).
        self.assertEqual(script.count('--network bridge'), 1)
        self.assertIn('start_egress', script)

    def test_run_senza_net_resta_isolato(self):
        script = read(SCRIPT)
        self.assertIn('egress_env=(--network none)', script)

    def test_allow_pip_richiede_net_in_run(self):
        r = self.run_cmd('--allow-pip', '--', 'true')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--allow-pip richiede --net', r.stderr)

    def test_allow_pip_richiede_net_in_agent(self):
        r = self.agent('--allow-pip', '--', 'true')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--allow-pip richiede --net', r.stderr)

    def test_filtro_pattern_ancorati_e_pip_solo_su_richiesta(self):
        func = extract_function(read(SCRIPT), 'write_filter_file')
        with tempfile.TemporaryDirectory() as d:
            allow = os.path.join(d, 'allowlist.txt')
            with open(allow, 'w') as f:
                f.write('# commento\n\napi.anthropic.com\n')
            out = os.path.join(d, 'filter.conf')
            wrapper = (
                f'ALLOWLIST={allow}\n'
                'PIP_HOSTS=(pypi.org files.pythonhosted.org)\n'
                f'{func}\n'
                f'write_filter_file "$1" {out}\n'
            )
            subprocess.run(['bash', '-c', wrapper, 'bash', '0'], check=True, timeout=10)
            self.assertEqual(read(out), r'^api\.anthropic\.com$' + '\n')

            subprocess.run(['bash', '-c', wrapper, 'bash', '1'], check=True, timeout=10)
            lines = read(out).splitlines()
            self.assertIn(r'^api\.anthropic\.com$', lines)
            self.assertIn(r'^pypi\.org$', lines)
            self.assertIn(r'^files\.pythonhosted\.org$', lines)


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
