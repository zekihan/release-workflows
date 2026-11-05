import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


def fixture_archive(directory):
    root = Path(directory) / 'layout'
    (root / 'blobs/sha256').mkdir(parents=True)

    def blob(data, media, **extra):
        digest = hashlib.sha256(data).hexdigest()
        (root / 'blobs/sha256' / digest).write_bytes(data)
        return {'mediaType': media, 'digest': f'sha256:{digest}', 'size': len(data), **extra}

    def document(value, media, **extra):
        return blob(json.dumps(value, separators=(',', ':')).encode(), media, **extra)

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w'):
        pass
    layer = blob(buffer.getvalue(), 'application/vnd.oci.image.layer.v1.tar')
    manifests = []
    for arch in ['amd64', 'arm64']:
        config = document({'architecture': arch, 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': [layer['digest']]},
                           'config': {'Labels': {'org.opencontainers.image.revision': 'a' * 40,
                                                 'org.opencontainers.image.version': '1.0.0',
                                                 'io.zekihan.build-inputs': 'fixture'}}},
                          'application/vnd.oci.image.config.v1+json')
        manifests.append(document({'schemaVersion': 2, 'config': config, 'layers': [layer]},
                                  'application/vnd.oci.image.manifest.v1+json', platform={'os': 'linux', 'architecture': arch}))
    index = document({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': manifests},
                     'application/vnd.oci.image.index.v1+json')
    (root / 'index.json').write_text(json.dumps({'schemaVersion': 2, 'manifests': [index]}))
    (root / 'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
    archive = Path(directory) / 'image.tar'
    with tarfile.open(archive, 'w') as output:
        for path in root.rglob('*'):
            output.add(path, arcname=path.relative_to(root), recursive=False)
    return archive


@unittest.skipUnless(os.environ.get('RUN_REGISTRY_TESTS') == '1', 'Set RUN_REGISTRY_TESTS=1 for disposable local registry')
class RegistryTests(unittest.TestCase):
    def test_multiarch_archive_copies_preserve_digest_and_can_be_retried(self):
        endpoint = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
        self.assertTrue(endpoint.startswith('unix://'), 'Local Docker socket required')
        container = subprocess.check_output(['docker', 'run', '-d', '--rm', '-p', '127.0.0.1::5000', 'registry:2'], text=True).strip()
        try:
            port = subprocess.check_output(['docker', 'port', container, '5000/tcp'], text=True).strip()
            with tempfile.TemporaryDirectory() as directory:
                archive = fixture_archive(directory)
                source = f'oci-archive:{archive}'
                original = json.loads(subprocess.check_output(['skopeo', 'inspect', source]))
                for target in ['primary', 'mirror', 'primary']:
                    ref = f'docker://{port}/{target}:edge'
                    subprocess.run(['skopeo', 'copy', '--all', '--preserve-digests', '--dest-tls-verify=false', source, ref], check=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    copied = json.loads(subprocess.check_output(['skopeo', 'inspect', '--tls-verify=false', ref]))
                    self.assertEqual(copied['Digest'], original['Digest'])
                    index = json.loads(subprocess.check_output(['skopeo', 'inspect', '--tls-verify=false', '--raw', ref]))
                    self.assertEqual({x['platform']['architecture'] for x in index['manifests']}, {'amd64', 'arm64'})
        finally:
            subprocess.run(['docker', 'stop', container], check=True, stdout=subprocess.PIPE)
