"""Pure publishing decisions, also embedded verbatim in reusable workflows."""
import re


def stable_version(value):
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('Expected a stable X.Y.Z version')
    return tuple(map(int, value.split('.')))


def validate_version_history(version, tags):
    candidate = stable_version(version)
    for tag in tags:
        if re.fullmatch(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', tag):
            if stable_version(tag[1:]) > candidate:
                raise ValueError('Release version is older than a stable tag in its source history')


def release_identity(version, revision=None):
    if revision is None:
        stable_version(version)
        return version
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,110}', version):
        raise ValueError('Invalid upstream image version')
    if not re.fullmatch(r'[1-9]\d*', str(revision)):
        raise ValueError('Packaging revision must be a positive integer')
    return f'{version}-r{revision}'


def build_decision(*, manual, force, failed, source_matches, mirrors_match):
    if force and not manual:
        raise ValueError('Force is only allowed on manual runs')
    if force:
        return 'build'
    if source_matches and mirrors_match:
        return 'skip-success'
    if failed and not manual:
        return 'skip-failure'
    return 'build'


def immutable_decision(existing, candidate):
    if existing is None:
        return 'publish'
    if existing == candidate:
        return 'skip'
    raise ValueError('Immutable version already contains a different digest')


def latest_decision(existing_version, candidate_version):
    candidate = stable_version(candidate_version)
    if existing_version is None:
        return True
    return candidate > stable_version(existing_version)


def assert_forward(current, candidate, is_ancestor):
    if current is not None and current != candidate and not is_ancestor(current, candidate):
        raise ValueError('Refusing to replace a newer or unrelated source revision')


def validate_platforms(index, required):
    actual = {f"{m.get('platform', {}).get('os')}/{m.get('platform', {}).get('architecture')}"
              for m in index.get('manifests', [])}
    missing = set(required.split(',')) - actual
    if missing:
        raise ValueError(f'Missing image platforms: {sorted(missing)}')
