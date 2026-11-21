import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]

class DecideTests(unittest.TestCase):
    def decide(self, *, conclusion=None, event='schedule', attempt='1', force='false', unknown=False,
               current=None, history_unavailable=False, mode='edge', version_file=None):
        namespace = {}
        for script in ['policy.py', 'registry.py']:
            exec(compile((ROOT / 'scripts' / script).read_text(), script, 'exec'), namespace)
        exec(compile((ROOT / 'scripts/decide.py').read_text().removesuffix('decide()\n'), 'decide.py', 'exec'), namespace)
        def api(endpoint):
            if history_unavailable:
                raise RuntimeError('Workflow run history unavailable')
            if endpoint.endswith('/runs/2'): return {'workflow_id': 17}
            self.assertIn('/workflows/17/runs?', endpoint)
            return {'workflow_runs': [{'id': 1, 'conclusion': conclusion}] if conclusion else []}
        def inspect(ref):
            if unknown: raise RuntimeError('lookup failed')
            return current
        namespace.update(github=api, inspect_image=inspect)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'output'
            env = {'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': event, 'MODE': 'edge',
                   'GITHUB_SHA': 'a' * 40, 'IMAGE': 'app', 'GITHUB_REPOSITORY_OWNER': 'owner',
                   'DOCKER_USERNAME': 'owner', 'GITHUB_REPOSITORY': 'owner/app', 'GITHUB_RUN_ID': '2',
                   'GITHUB_RUN_ATTEMPT': attempt, 'FORCE': force, 'GITHUB_OUTPUT': str(out), 'INPUT_PATHS': '[]'}
            env['MODE'] = mode
            if version_file:
                path = Path(directory) / 'config.yaml'
                path.write_text(version_file)
                env['VERSION_FILE'] = str(path)
            with patch.dict(os.environ, env):namespace['decide']()
            return dict(line.split('=', 1) for line in out.read_text().splitlines())

    def test_failed_cancelled_and_timed_out_revisions_do_not_retry(self):
        for conclusion in ['failure', 'cancelled', 'timed_out']:
            self.assertEqual(self.decide(conclusion=conclusion)['build'], 'false')

    def test_declarative_identity_uses_revision_only_when_declared(self):
        upstream = 'version: v1.8.2 # renovate: depName=x\narch: linux/amd64\nrevision: 2\n'
        self.assertEqual(self.decide(mode='declarative', version_file=upstream)['version'], 'v1.8.2-r2')
        own = 'version: 1.0.1\narch: linux/amd64\n'
        self.assertEqual(self.decide(mode='declarative', version_file=own)['version'], '1.0.1')
        with self.assertRaises(ValueError):
            self.decide(mode='declarative', version_file='version: v1.0.1\n')

    def test_explicit_recovery_and_new_revision_build(self):
        self.assertEqual(self.decide(conclusion='failure', event='workflow_dispatch')['build'], 'true')
        self.assertEqual(self.decide(conclusion='failure', attempt='2')['build'], 'true')
        self.assertEqual(self.decide()['build'], 'true')

    def test_lookup_error_is_never_assumed_success(self):
        self.assertEqual(self.decide(unknown=True)['build'], 'true')

    def test_unchanged_mirrors_skip_without_workflow_run_history(self):
        current = {'digest': 'published', 'revision': 'a' * 40, 'fingerprint': 'a' * 40}
        self.assertEqual(self.decide(current=current, history_unavailable=True)['build'], 'false')

    def test_explicit_recovery_builds_without_workflow_run_history(self):
        for recovery in ({'event': 'workflow_dispatch'}, {'attempt': '2'}):
            with self.subTest(**recovery):
                self.assertEqual(self.decide(history_unavailable=True, **recovery)['build'], 'true')

    def test_scheduled_build_requires_workflow_run_history(self):
        with self.assertRaisesRegex(RuntimeError, 'Workflow run history unavailable'):
            self.decide(history_unavailable=True)
