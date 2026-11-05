"""Trusted registry and GitHub clients embedded into the workflow definition."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess


def command(args, *, missing=False):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        error = result.stderr.decode(errors='replace')
        if missing and any(marker in error.lower() for marker in ('manifest unknown', 'name unknown', 'manifest_unknown')):
            return None
        # Do not echo command arguments or registry responses containing credentials.
        raise RuntimeError(f'{args[0]} failed with exit code {result.returncode}')
    return result.stdout


def inspect_image(reference):
    raw = command(['skopeo', 'inspect', '--raw', reference], missing=True)
    if raw is None:
        return None
    metadata = json.loads(command(['skopeo', 'inspect', '--override-os', 'linux', '--override-arch', 'amd64', reference]))
    return {'digest': metadata['Digest'], 'revision': metadata.get('Labels', {}).get('org.opencontainers.image.revision'),
            'version': metadata.get('Labels', {}).get('org.opencontainers.image.version'),
            'fingerprint': metadata.get('Labels', {}).get('io.zekihan.build-inputs'), 'index': json.loads(raw)}


def github(endpoint):
    return json.loads(command(['gh', 'api', endpoint]))


def ancestor(old, new):
    if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (old, new)):
        return False
    return subprocess.run(['git', 'merge-base', '--is-ancestor', old, new], check=False).returncode == 0


def output(values):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        for name, value in values.items():
            value = str(value)
            if '\n' in value or '\r' in value:
                raise ValueError('Multiline workflow output rejected')
            stream.write(f'{name}={value}\n')


def image_names():
    image = os.environ['IMAGE']
    owner = os.environ['GITHUB_REPOSITORY_OWNER'].lower()
    docker_owner = os.environ['DOCKER_USERNAME']
    for value in (image, owner, docker_owner):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*', value):
            raise ValueError('Invalid image or registry namespace')
    return [f'ghcr.io/{owner}/{image}', f'docker.io/{docker_owner}/{image}']


def publish_copy(source, destination):
    command(['skopeo', 'copy', '--all', '--preserve-digests', source, destination])


def verify_access(reference, digest):
    if os.environ.get('PUBLIC_IMAGE') == 'true':
        remote = command(['skopeo', 'inspect', '--no-creds', '--format', '{{.Digest}}', reference]).decode().strip()
        if remote != digest:
            raise ValueError('Anonymous pull does not resolve to the published image')


def verify_archive(path, expected):
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('Missing or invalid expected artifact checksum')
    with Path(path).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected:
        raise ValueError('Artifact checksum does not match build job output')
