import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]


class PublicationTests(unittest.TestCase):
    def test_conflict_is_detected_before_either_registry_changes(self):
        writes = self.run_publish(mode='release', existing={'docker.io/owner/app:1.0.0': self.meta('old')}, error=True)
        self.assertEqual(writes, [])

    def test_release_does_not_promote_latest_before_finalization(self):
        writes = self.run_publish(mode='release')
        self.assertEqual({destination.rsplit(':', 1)[1] for _, destination in writes}, {'1.0.0'})

    def test_declarative_dependency_refresh_preserves_release(self):
        existing = {f'{registry}/owner/app:1.0.0': self.meta('released') for registry in ['ghcr.io', 'docker.io']}
        writes = self.run_publish(mode='declarative', existing=existing)
        self.assertTrue(any(destination.endswith(':edge') for _, destination in writes))
        self.assertFalse(any(destination.endswith(':1.0.0') for _, destination in writes))

    def test_missing_immutable_mirror_uses_published_digest(self):
        writes = self.run_publish(mode='declarative', existing={'ghcr.io/owner/app:1.0.0': self.meta('released')})
        mirrored = [(source, dest) for source, dest in writes if dest.endswith(':1.0.0')]
        self.assertEqual(mirrored, [('docker://ghcr.io/owner/app@released', 'docker://docker.io/owner/app:1.0.0')])

    def test_stale_edge_changes_neither_registry(self):
        old = self.meta('newer') | {'revision': 'b' * 40}
        self.assertEqual(self.run_publish(mode='edge', existing={'ghcr.io/owner/app:edge': old}, error=True), [])

    @staticmethod
    def meta(digest):
        return {'digest': digest, 'revision': 'a' * 40, 'version': '1.0.0', 'fingerprint': 'f',
                'index': {'manifests': [{'platform': {'os': 'linux', 'architecture': arch}} for arch in ['amd64', 'arm64']]}}

    def run_publish(self, *, mode, existing=None, error=False):
        namespace = {}
        for script in ['policy.py', 'registry.py']:
            exec(compile((ROOT / 'scripts' / script).read_text(), script, 'exec'), namespace)
        state = dict(existing or {})
        writes = []
        candidate = self.meta('candidate')

        def inspect(ref):
            if ref.startswith('oci-archive:'):
                return candidate
            return state.get(ref.removeprefix('docker://'))

        def copy(source, destination):
            writes.append((source, destination))
            if source.startswith('oci-archive:'):
                value = candidate
            else:
                digest = source.rsplit('@', 1)[1]
                value = next(x for x in [candidate, *state.values()] if x['digest'] == digest)
            state[destination.removeprefix('docker://')] = value

        namespace.update(inspect_image=inspect, publish_copy=copy, ancestor=lambda old, new: old == new)
        with tempfile.TemporaryDirectory() as directory:
            before = Path.cwd()
            os.chdir(directory)
            try:
                Path('artifact').mkdir()
                Path('artifact/image.tar').write_bytes(b'fixture')
                env = {'VERSION': '' if mode == 'edge' else '1.0.0', 'IMAGE': 'app', 'MODE': mode,
                       'GITHUB_SHA': 'a' * 40, 'FINGERPRINT': 'f', 'PLATFORMS': 'linux/amd64,linux/arm64',
                       'GITHUB_REF': 'refs/tags/v1.0.0' if mode == 'release' else 'refs/heads/main',
                       'GITHUB_REPOSITORY_OWNER': 'owner', 'DOCKER_USERNAME': 'owner',
                       'GITHUB_OUTPUT': 'outputs', 'GITHUB_STEP_SUMMARY': 'summary',
                       'ARCHIVE_SHA256': hashlib.sha256(b'fixture').hexdigest()}
                with patch.dict(os.environ, env):
                    script = compile((ROOT / 'scripts/promote.py').read_text(), 'promote.py', 'exec')
                    if error:
                        with self.assertRaises(ValueError):
                            exec(script, namespace)
                    else:
                        exec(script, namespace)
            finally:
                os.chdir(before)
        return writes


if __name__ == '__main__':
    unittest.main()
