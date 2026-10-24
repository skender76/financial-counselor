"""Il hook di progetto per Claude Code (.claude/settings.json) chiama la guardia."""

import json
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(ROOT, '.claude', 'settings.json')


class ClaudeHookTest(unittest.TestCase):
    def setUp(self):
        with open(SETTINGS, encoding='utf-8') as f:
            self.settings = json.load(f)

    def hook_command(self):
        entries = self.settings['hooks']['PreToolUse']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['matcher'], '*')  # tutti gli strumenti, file compresi
        hooks = entries[0]['hooks']
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]['type'], 'command')
        return hooks[0]['command']

    def test_chiama_la_guardia_in_modalita_hook(self):
        self.assertEqual(self.hook_command(),
                         '"$CLAUDE_PROJECT_DIR"/sandbox/require-sandbox.sh --hook')

    def test_fuori_dal_sandbox_il_comando_blocca_con_2(self):
        env = {k: v for k, v in os.environ.items() if k != 'DASHBOARD_ALLOW_HOST'}
        env['CLAUDE_PROJECT_DIR'] = ROOT
        r = subprocess.run(['sh', '-c', self.hook_command()], capture_output=True,
                           text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn('Bloccato', r.stderr)

    def test_la_guardia_e_eseguibile(self):
        self.assertTrue(os.access(os.path.join(ROOT, 'sandbox', 'require-sandbox.sh'), os.X_OK))

    def test_nessun_altro_hook_o_permesso_nel_file(self):
        self.assertEqual(set(self.settings), {'hooks'})
        self.assertEqual(set(self.settings['hooks']), {'PreToolUse'})


if __name__ == '__main__':
    unittest.main()
