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


def inspect_image(reference, *, anonymous=False):
    args = ['skopeo', 'inspect']
    if anonymous:
        args.append('--no-creds')
    raw = command([*args, '--raw', reference], missing=True)
    if raw is None:
        return None
    index = json.loads(raw)
    platforms = [item.get('platform') or {} for item in index.get('manifests', [])]
    platforms = [p for p in platforms if all(p.get(key) not in (None, '', 'unknown') for key in ('os', 'architecture'))]
    default = {'os': 'linux', 'architecture': 'amd64'}
    platform = next((p for p in platforms if (p['os'], p['architecture']) == ('linux', 'amd64')),
                    platforms[0] if platforms else default)
    args += ['--override-os', platform['os'], '--override-arch', platform['architecture']]
    if platform.get('variant'):
        args += ['--override-variant', platform['variant']]
    metadata = json.loads(command([*args, '--no-tags', reference]))
    labels = metadata.get('Labels') or {}
    return {'digest': metadata['Digest'], 'revision': labels.get('org.opencontainers.image.revision'),
            'version': labels.get('org.opencontainers.image.version'),
            'fingerprint': labels.get('io.zekihan.build-inputs'), 'index': index,
            'platform': f"{metadata.get('Os')}/{metadata.get('Architecture')}"}


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
        remote = inspect_image(reference, anonymous=True)
        if not remote or remote['digest'] != digest:
            raise ValueError('Anonymous pull does not resolve to the published image')


def current_release_version(current):
    if current is None:
        return None
    value = (current.get('version') or '').removeprefix('v')
    try:
        stable_version(value)
        return value
    except ValueError:
        # Older metadata-action tags recorded "0" or "latest". Resolve their
        # declared version from the exact labelled source, never a branch head.
        revision = current.get('revision') or ''
        if not re.fullmatch(r'[0-9a-f]{40}', revision):
            raise ValueError('Existing latest has no verifiable version/source metadata') from None
        value = command(['git', 'show', f'{revision}:VERSION']).decode().strip()
        stable_version(value)
        return value


def verify_archive(path, expected):
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('Missing or invalid expected artifact checksum')
    with Path(path).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected:
        raise ValueError('Artifact checksum does not match build job output')
