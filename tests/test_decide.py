import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]

class DecideTests(unittest.TestCase):
    def decide(self, *, conclusion=None, event='schedule', attempt='1', force='false', unknown=False):
        namespace = {}
        for script in ['policy.py', 'registry.py']:
            exec(compile((ROOT / 'scripts' / script).read_text(), script, 'exec'), namespace)
        exec(compile((ROOT / 'scripts/decide.py').read_text().removesuffix('decide()\n'), 'decide.py', 'exec'), namespace)
        def api(endpoint):
            if endpoint.endswith('/runs/2'): return {'workflow_id': 17}
            self.assertIn('/workflows/17/runs?', endpoint)
            return {'workflow_runs': [{'id': 1, 'conclusion': conclusion}] if conclusion else []}
        def inspect(ref):
            if unknown: raise RuntimeError('lookup failed')
            return None
        namespace.update(github=api, inspect_image=inspect)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'output'
            env = {'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': event, 'MODE': 'edge',
                   'GITHUB_SHA': 'a' * 40, 'IMAGE': 'app', 'GITHUB_REPOSITORY_OWNER': 'owner',
                   'DOCKER_USERNAME': 'owner', 'GITHUB_REPOSITORY': 'owner/app', 'GITHUB_RUN_ID': '2',
                   'GITHUB_RUN_ATTEMPT': attempt, 'FORCE': force, 'GITHUB_OUTPUT': str(out)}
            with patch.dict(os.environ, env):namespace['decide']()
            return dict(line.split('=', 1) for line in out.read_text().splitlines())

    def test_failed_cancelled_and_timed_out_revisions_do_not_retry(self):
        for conclusion in ['failure', 'cancelled', 'timed_out']:
            self.assertEqual(self.decide(conclusion=conclusion)['build'], 'false')

    def test_explicit_recovery_and_new_revision_build(self):
        self.assertEqual(self.decide(conclusion='failure', event='workflow_dispatch')['build'], 'true')
        self.assertEqual(self.decide(conclusion='failure', attempt='2')['build'], 'true')
        self.assertEqual(self.decide()['build'], 'true')

    def test_lookup_error_is_never_assumed_success(self):
        self.assertEqual(self.decide(unknown=True)['build'], 'true')
