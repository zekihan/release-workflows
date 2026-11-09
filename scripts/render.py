"""Embed reviewed helpers in workflows so caller SHA pins cover executable code."""
from pathlib import Path
import copy
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    'checkout': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
    'go': 'actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e',
    'node': 'actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1',
    'goreleaser': 'goreleaser/goreleaser-action@a4f02ab5355a589de60d917a659ce617ed19b3de',
    'qemu': 'docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1',
    'buildx': 'docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069',
    'build': 'docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc',
    'login': 'docker/login-action@81c6f93d4bc991e510b342a00b419b3bb4efc5c6',
    'upload': 'actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9',
    'download': 'actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333',
    'helm': 'azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310',
}


def literal(dumper, value):
    return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|' if '\n' in value else None)


yaml.SafeDumper.add_representer(str, literal)


def inp(default='', kind='string'):
    return {'type': kind, 'default': default}


def embedded(script):
    text = '\n'.join((ROOT / 'scripts' / name).read_text() for name in ['policy.py', 'registry.py', script])
    return "python3 - <<'PUBLISH_POLICY'\n" + text + '\nPUBLISH_POLICY\n'


def action(pin, **kwargs):
    return {'uses': PINS[pin], **kwargs}


def checkout():
    return action('checkout', **{'with': {'ref': '${{ github.sha }}', 'fetch-depth': 0, 'persist-credentials': False}})


def registry_setup():
    return [
        {'run': 'if ! command -v skopeo >/dev/null; then\n  sudo apt-get update -qq\n  sudo apt-get install -y --no-install-recommends skopeo\nfi\n'},
        action('login', **{'with': {'registry': 'ghcr.io', 'username': '${{ github.actor }}', 'password': '${{ github.token }}'}}),
        action('login', **{'with': {'username': '${{ secrets.DOCKER_USERNAME }}', 'password': '${{ secrets.DOCKER_PASSWORD }}'}}),
    ]


def tools_setup():
    return [
        action('go', **{'if': "${{ hashFiles('go.mod') != '' }}", 'with': {'go-version-file': 'go.mod', 'cache': False}}),
        action('node', **{'if': "${{ inputs.node-version != '' }}", 'with': {'node-version': '${{ inputs.node-version }}'}}),
        action('goreleaser', **{'if': "${{ hashFiles('.goreleaser.yaml') != '' }}", 'with': {'version': '${{ inputs.goreleaser-version }}', 'install-only': True}}),
    ]


secrets = {name: {'required': True} for name in ['DOCKER_USERNAME', 'DOCKER_PASSWORD']}
common_env = {'GH_TOKEN': '${{ github.token }}', 'IMAGE': '${{ inputs.image }}',
              'DOCKER_USERNAME': '${{ secrets.DOCKER_USERNAME }}', 'MODE': '${{ inputs.mode }}'}

decide = {
    'name': 'Decide publication',
    'on': {'workflow_call': {'inputs': {'image': inp(), 'mode': inp('edge'), 'version-file': inp('VERSION'),
                                      'input-paths': inp('[]'), 'force': inp(False, 'boolean')},
                             'secrets': {key: {'required': False} for key in secrets},
                             'outputs': {name: {'value': '${{ jobs.decide.outputs.' + name + ' }}'} for name in ['build', 'version', 'fingerprint']}}},
    'permissions': {'contents': 'read', 'actions': 'read', 'packages': 'read'},
    'jobs': {'decide': {'runs-on': 'ubuntu-latest', 'timeout-minutes': 10,
                        'outputs': {name: '${{ steps.decide.outputs.' + name + ' }}' for name in ['build', 'version', 'fingerprint']},
                        'steps': [checkout(), *[step | {'if': "${{ inputs.image != '' }}"} for step in registry_setup()], {'id': 'decide', 'env': common_env | {
                            'VERSION_FILE': '${{ inputs.version-file }}', 'INPUT_PATHS': '${{ inputs.input-paths }}',
                            'FORCE': '${{ inputs.force }}'}, 'run': embedded('decide.py')}]}}
}

image = {
    'name': 'Build and publish image',
    'on': {'workflow_call': {'inputs': {
        'image': inp(), 'mode': inp('edge'), 'version': inp(), 'fingerprint': inp(),
        'context': inp('.'), 'platforms': inp('linux/amd64,linux/arm64'),
        'prepare-command': inp('true'), 'node-version': inp(), 'goreleaser-version': inp('v2.20.0'),
        'qemu': inp(False, 'boolean'), 'native-arm': inp(False, 'boolean'),
        'smoke-command': inp('true'), 'public-image': inp(True, 'boolean')}, 'secrets': secrets,
        'outputs': {'digest': {'value': '${{ jobs.publish.outputs.digest }}'}}}},
    'permissions': {'contents': 'read'},
    'jobs': {
        'build': {'runs-on': 'ubuntu-latest', 'timeout-minutes': 60,
                  'outputs': {'artifact': '${{ steps.artifact.outputs.artifact-id }}', 'sha256': '${{ steps.checksum.outputs.sha256 }}'},
                  'steps': [checkout(), *tools_setup(),
                      {'name': 'Prepare image inputs without publication credentials', 'env': {
                          'PREPARE': '${{ inputs.prepare-command }}', 'MODE': '${{ inputs.mode }}', 'RELEASE_VERSION': '${{ inputs.version }}'},
                       'run': 'bash -euo pipefail -c "$PREPARE"'},
                      action('qemu', **{'if': '${{ inputs.qemu }}'}), action('buildx'),
                      action('build', **{'with': {
                          'context': '${{ inputs.context }}', 'platforms': '${{ inputs.platforms }}', 'push': False,
                          'outputs': 'type=oci,dest=${{ runner.temp }}/image.tar',
                          'build-args': 'VERSION=${{ inputs.version || github.sha }}',
                          'labels': 'org.opencontainers.image.revision=${{ github.sha }}\norg.opencontainers.image.version=${{ inputs.version || github.sha }}\nio.zekihan.build-inputs=${{ inputs.fingerprint }}\n',
                          'cache-from': 'type=gha,scope=${{ inputs.image }}', 'cache-to': 'type=gha,mode=max,scope=${{ inputs.image }}'}}),
                      {'id': 'checksum', 'run': 'echo "sha256=$(sha256sum "$RUNNER_TEMP/image.tar" | cut -d " " -f 1)" >> "$GITHUB_OUTPUT"'},
                      action('upload', **{'id': 'artifact', 'with': {'name': 'image-${{ inputs.image }}', 'path': '${{ runner.temp }}/image.tar',
                                                                  'if-no-files-found': 'error', 'retention-days': 7}})]},
        'publish': {'needs': 'build', 'runs-on': 'ubuntu-latest', 'timeout-minutes': 30,
                    'permissions': {'contents': 'read', 'packages': 'write'}, 'outputs': {'digest': '${{ steps.publish.outputs.digest }}'},
                    'steps': [checkout(), action('download', **{'with': {'artifact-ids': '${{ needs.build.outputs.artifact }}', 'path': 'artifact', 'merge-multiple': True}}),
                              *registry_setup(), {'id': 'publish', 'env': common_env | {
                                  'VERSION': '${{ inputs.version }}', 'FINGERPRINT': '${{ inputs.fingerprint }}',
                                  'PUBLIC_IMAGE': '${{ inputs.public-image }}',
                                  'PLATFORMS': '${{ inputs.platforms }}', 'ARCHIVE_SHA256': '${{ needs.build.outputs.sha256 }}'},
                                                  'run': embedded('promote.py')}]}
    }
}


image['jobs']['build']['steps'][-3:-3] = [
    {'name': 'Smoke-test the native image from the prepared archive',
     'if': "${{ inputs.smoke-command != 'true' }}",
     'env': {'SMOKE': '${{ inputs.smoke-command }}'},
     'run': 'sudo apt-get update -qq\nsudo apt-get install -y --no-install-recommends skopeo\nskopeo copy --override-os linux --override-arch "$(go env GOARCH)" oci-archive:"$RUNNER_TEMP/image.tar" docker-daemon:local/publishing-smoke:latest\nbash -euo pipefail -c "$SMOKE"\n'}]
# The extra smoke step belongs after the image build, before checksum/upload.
steps = image['jobs']['build']['steps']
smoke = next(s for s in steps if s.get('name', '').startswith('Smoke-test'))
steps.remove(smoke)
steps.insert(-2, smoke)
arm = copy.deepcopy(image['jobs']['build'])
arm['if'] = '${{ inputs.native-arm }}'
arm['runs-on'] = 'ubuntu-26.04-arm'
for step in arm['steps']:
    if step.get('uses') == PINS['build']:
        step['with']['platforms'] = 'linux/arm64'
        step['with']['cache-from'] += '-arm64'
        step['with']['cache-to'] += '-arm64'
    if step.get('uses') == PINS['upload']:
        step['with']['name'] += '-arm64'
image['jobs']['build-arm'] = arm
for step in image['jobs']['build']['steps']:
    if step.get('uses') == PINS['build']:
        step['with']['platforms'] = "${{ inputs.native-arm && 'linux/amd64' || inputs.platforms }}"
publish = image['jobs']['publish']
publish['needs'] = ['build', 'build-arm']
publish['if'] = "${{ !cancelled() && needs.build.result == 'success' && (needs.build-arm.result == 'skipped' || needs.build-arm.result == 'success') }}"
publish['steps'][2:2] = [
    action('download', **{'if': '${{ inputs.native-arm }}', 'with': {
        'artifact-ids': '${{ needs.build-arm.outputs.artifact }}', 'path': 'arm-artifact', 'merge-multiple': True}}),
    {'id': 'merge', 'if': '${{ inputs.native-arm }}', 'env': {
        'AMD_SHA256': '${{ needs.build.outputs.sha256 }}', 'ARM_SHA256': '${{ needs.build-arm.outputs.sha256 }}'},
     'run': embedded('merge_oci.py')}]
publish['steps'][-1]['env']['ARCHIVE_SHA256'] = '${{ inputs.native-arm && steps.merge.outputs.sha256 || needs.build.outputs.sha256 }}'

release = {
    'name': 'Publish stable release',
    'on': {'workflow_call': {'inputs': {
        'version': inp(), 'image': inp(), 'image-digest': inp(), 'chart-path': inp(), 'chart-name': inp(),
        'binaries': inp(True, 'boolean'), 'github-release': inp(True, 'boolean'),
        'prepare-command': inp('true'), 'verify-command': inp('true'), 'notes-file': inp(),
        'node-version': inp(), 'goreleaser-version': inp('v2.20.0')},
        'secrets': {key: {'required': False} for key in secrets}}},
    'permissions': {'contents': 'read'},
    'jobs': {
        'build': {'runs-on': 'ubuntu-latest', 'timeout-minutes': 45,
                  'outputs': {'artifact': '${{ steps.artifact.outputs.artifact-id }}', 'sha256': '${{ steps.manifest.outputs.sha256 }}'},
                  'steps': [checkout(), *tools_setup(),
                      {'name': 'Prepare release without publication credentials', 'env': {'PREPARE': '${{ inputs.prepare-command }}',
                          'MODE': 'release', 'RELEASE_VERSION': '${{ inputs.version }}'}, 'run': 'bash -euo pipefail -c "$PREPARE"'},
                      {'if': '${{ inputs.binaries }}', 'run': 'goreleaser release --clean --skip=publish,announce'},
                      {'env': {'VERIFY': '${{ inputs.verify-command }}'}, 'run': 'bash -euo pipefail -c "$VERIFY"'},
                      action('helm', **{'if': "${{ inputs.chart-path != '' }}", 'with': {'version': 'v4.3.1'}}),
                      {'if': "${{ inputs.chart-path != '' }}", 'env': {'CHART_PATH': '${{ inputs.chart-path }}', 'VERSION': '${{ inputs.version }}'},
                       'run': 'helm lint "$CHART_PATH"\nhelm package "$CHART_PATH" --version "$VERSION" --app-version "$VERSION" --destination dist\n'},
                      {'id': 'manifest', 'env': {'NOTES_FILE': '${{ inputs.notes-file }}'}, 'run': '''python3 - <<'PY'
import hashlib, json, os, pathlib, shutil
dest = pathlib.Path('release-assets')
dest.mkdir(exist_ok=True)
manifest = {}
for pattern in ('*.tar.gz', '*.zip', '*.tgz', 'checksums.txt', 'CHANGELOG.md'):
    for path in pathlib.Path('dist').glob(pattern):
        shutil.copyfile(path, dest / path.name)
        with path.open('rb') as stream:
            manifest[path.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
notes = os.environ.get('NOTES_FILE')
if notes:
    shutil.copyfile(notes, dest / 'CHANGELOG.md')
    manifest['CHANGELOG.md'] = hashlib.sha256((dest / 'CHANGELOG.md').read_bytes()).hexdigest()
if manifest:
    checksums = ''.join(f'{digest}  {name}\\n' for name, digest in sorted(manifest.items()) if name != 'checksums.txt')
    (dest / 'SHA256SUMS').write_text(checksums)
    manifest['SHA256SUMS'] = hashlib.sha256(checksums.encode()).hexdigest()
data = json.dumps(manifest, sort_keys=True).encode()
(dest / 'manifest.json').write_bytes(data)
with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
    output.write('sha256=' + hashlib.sha256(data).hexdigest() + '\\n')
PY
'''},
                      action('upload', **{'id': 'artifact', 'with': {'name': 'release-assets', 'path': 'release-assets/',
                          'if-no-files-found': 'error', 'retention-days': 7}})]},
        'publish': {'needs': 'build', 'runs-on': 'ubuntu-latest', 'timeout-minutes': 30,
                    'permissions': {'contents': 'write', 'packages': 'write'},
                    'steps': [checkout(),
                        action('download', **{'with': {'artifact-ids': '${{ needs.build.outputs.artifact }}', 'path': 'artifact', 'merge-multiple': True}}),
                        {'run': 'if ! command -v skopeo >/dev/null; then\n  sudo apt-get update -qq\n  sudo apt-get install -y --no-install-recommends skopeo\nfi\n'},
                        *[step | {'if': "${{ inputs.image != '' }}"} for step in registry_setup()[1:]],
                        action('helm', **{'if': "${{ inputs.chart-path != '' }}", 'with': {'version': 'v4.3.1'}}),
                        {'if': "${{ inputs.chart-path != '' }}", 'env': {'TOKEN': '${{ github.token }}'},
                         'run': 'printf "%s" "$TOKEN" | helm registry login ghcr.io --username "$GITHUB_ACTOR" --password-stdin'},
                        {'env': {'GH_TOKEN': '${{ github.token }}', 'GH_REPO': '${{ github.repository }}',
                            'VERSION': '${{ inputs.version }}', 'IMAGE': '${{ inputs.image }}', 'IMAGE_DIGEST': '${{ inputs.image-digest }}',
                            'DOCKER_USERNAME': '${{ secrets.DOCKER_USERNAME }}', 'CHART_NAME': '${{ inputs.chart-name }}',
                            'GITHUB_RELEASE': '${{ inputs.github-release }}', 'MANIFEST_SHA256': '${{ needs.build.outputs.sha256 }}'},
                         'run': embedded('finalize.py')}]}
    }
}

# Verify the build job's checksum before introducing registry credentials.
for workflow in [image, release]:
    steps = workflow['jobs']['publish']['steps']
    first_login = next(i for i, step in enumerate(steps) if step.get('uses') == PINS['login'])
    checksum = '${{ inputs.native-arm && steps.merge.outputs.sha256 || needs.build.outputs.sha256 }}' if workflow is image else '${{ needs.build.outputs.sha256 }}'
    path = 'artifact/image.tar' if workflow is image else 'artifact/manifest.json'
    steps.insert(first_login, {'name': 'Verify prepared artifact before registry login',
        'env': {'EXPECTED_SHA256': checksum, 'ARTIFACT_PATH': path},
        'run': '[[ "$EXPECTED_SHA256" =~ ^[0-9a-f]{64}$ ]]\nprintf "%s  %s\\n" "$EXPECTED_SHA256" "$ARTIFACT_PATH" | sha256sum --strict --check -\n'})

def main():
    workflows = {'decide.yaml': decide, 'image.yaml': image, 'release.yaml': release}
    failed = False
    for name, document in workflows.items():
        rendered = '# Generated by scripts/render.py; edit scripts and regenerate.\n' + yaml.safe_dump(document, sort_keys=False, width=120)
        path = ROOT / '.github/workflows' / name
        if '--check' in sys.argv:
            if not path.exists() or path.read_text() != rendered:
                print(f'{name}: generated workflow is stale')
                failed = True
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
