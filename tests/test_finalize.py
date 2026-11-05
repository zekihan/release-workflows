import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]

class FinalizeTests(unittest.TestCase):
    def run_finalize(self, latest, *, error=False):
        namespace = {}
        for script in ['policy.py', 'registry.py']:
            exec(compile((ROOT / 'scripts' / script).read_text(), script, 'exec'), namespace)
        release = {'digest': 'prepared', 'version': '2.0.0'}
        state = {f'{registry}/owner/app:2.0.0': release for registry in ['ghcr.io', 'docker.io']}
        state.update(latest)
        writes = []
        def inspect(ref):
            return state.get(ref.removeprefix('docker://'))
        def copy(src, dst):
            writes.append((src, dst))
            state[dst.removeprefix('docker://')] = release
        namespace.update(inspect_image=inspect, publish_copy=copy)
        with tempfile.TemporaryDirectory() as directory:
            before = Path.cwd()
            os.chdir(directory)
            try:
                Path('artifact').mkdir()
                Path('artifact/manifest.json').write_bytes(b'{}')
                env = {'VERSION': '2.0.0', 'GITHUB_REF': 'refs/tags/v2.0.0',
                       'MANIFEST_SHA256': hashlib.sha256(b'{}').hexdigest(), 'IMAGE': 'app',
                       'IMAGE_DIGEST': 'prepared', 'GITHUB_REPOSITORY_OWNER': 'owner',
                       'DOCKER_USERNAME': 'owner', 'GITHUB_RELEASE': 'false'}
                with patch.dict(os.environ, env):
                    script = compile((ROOT / 'scripts/finalize.py').read_text(), 'finalize.py', 'exec')
                    if error:
                        with self.assertRaises(ValueError): exec(script, namespace)
                    else:
                        exec(script, namespace)
            finally:
                os.chdir(before)
        return writes

    def test_old_release_never_moves_latest_backward(self):
        state = {f'{r}/owner/app:latest': {'digest': 'newer', 'version': '3.0.0'} for r in ['ghcr.io', 'docker.io']}
        self.assertEqual(self.run_finalize(state), [])

    def test_partial_latest_promotion_recovers_only_missing_mirror(self):
        state = {'ghcr.io/owner/app:latest': {'digest': 'prepared', 'version': '2.0.0'},
                 'docker.io/owner/app:latest': {'digest': 'old', 'version': '1.0.0'}}
        writes = self.run_finalize(state)
        self.assertEqual([dst for _, dst in writes], ['docker://docker.io/owner/app:latest'])

    def test_conflicting_same_version_changes_neither_registry(self):
        state = {'ghcr.io/owner/app:latest': {'digest': 'old', 'version': '1.0.0'},
                 'docker.io/owner/app:latest': {'digest': 'different', 'version': '2.0.0'}}
        self.assertEqual(self.run_finalize(state, error=True), [])

    def test_unknown_current_version_fails_closed(self):
        self.assertEqual(self.run_finalize({'ghcr.io/owner/app:latest': {'digest': 'old', 'version': None}}, error=True), [])
