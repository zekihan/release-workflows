import hashlib
from pathlib import Path
import tempfile
import unittest
import json
import subprocess
from test_registry import fixture_archive

ROOT = Path(__file__).parents[1]

class MergeTests(unittest.TestCase):
    def test_native_archives_merge_deterministically_and_preserve_platforms(self):
        namespace = {'__name__': 'test'}
        for script in ['policy.py', 'registry.py', 'merge_oci.py']:
            exec(compile((ROOT / 'scripts' / script).read_text(), script, 'exec'), namespace)
        with tempfile.TemporaryDirectory() as directory:
            source = fixture_archive(directory)
            checksum = hashlib.sha256(source.read_bytes()).hexdigest()
            first, second = Path(directory) / 'first.tar', Path(directory) / 'second.tar'
            namespace['merge_archives']([(source, checksum)], first)
            namespace['merge_archives']([(source, checksum)], second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            raw = subprocess.check_output(['skopeo', 'inspect', '--raw', f'oci-archive:{first}'])
            self.assertEqual({x['platform']['architecture'] for x in json.loads(raw)['manifests']}, {'amd64', 'arm64'})
            with self.assertRaisesRegex(ValueError, 'checksum'):
                namespace['merge_archives']([(source, '0' * 64)], first)
