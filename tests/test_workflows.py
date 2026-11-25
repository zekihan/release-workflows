import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

WORKFLOWS = Path(__file__).parents[1] / '.github/workflows'


class WorkflowConditionTests(unittest.TestCase):
    def test_reusable_workflows_select_runner(self):
        for name in ('decide', 'image', 'release', 'go-checks'):
            with self.subTest(workflow=name):
                workflow = yaml.load((WORKFLOWS / f'{name}.yaml').read_text(), Loader=yaml.BaseLoader)
                runner = workflow['on']['workflow_call']['inputs']['runs-on']
                self.assertEqual(runner, {'type': 'string', 'default': 'ubuntu-latest'})
                for job_name, job in workflow['jobs'].items():
                    with self.subTest(job=job_name):
                        if (name, job_name) == ('image', 'build-arm'):
                            self.assertRegex(job['runs-on'], r'^ubuntu-\d+\.\d+-arm$')
                        else:
                            self.assertEqual(job['runs-on'], '${{ inputs.runs-on }}')

    def test_goreleaser_setup_covers_default_configs_and_release_binaries(self):
        config_check = (
            "hashFiles('.config/goreleaser.yml', '.config/goreleaser.yaml', '.goreleaser.yml', "
            "'.goreleaser.yaml', 'goreleaser.yml', 'goreleaser.yaml') != ''"
        )
        for name, job, condition in (
            ('image', 'build', config_check),
            ('image', 'build-arm', config_check),
            ('release', 'build', f'inputs.binaries || {config_check}'),
        ):
            with self.subTest(workflow=name, job=job):
                workflow = yaml.safe_load((WORKFLOWS / f'{name}.yaml').read_text())
                step = next(step for step in workflow['jobs'][job]['steps']
                            if step.get('uses', '').startswith('goreleaser/goreleaser-action@'))
                self.assertEqual(step['if'], '${{ ' + condition + ' }}')

    def test_native_arm_artifact_and_cache_scope_do_not_collide_with_another_image(self):
        jobs = yaml.safe_load((WORKFLOWS / 'image.yaml').read_text())['jobs']
        for action, field in (
            ('actions/upload-artifact@', 'name'),
            ('docker/build-push-action@', 'cache-from'),
            ('docker/build-push-action@', 'cache-to'),
        ):
            with self.subTest(field=field):
                values = []
                for job, image in (('build', 'widget'), ('build-arm', 'widget'), ('build', 'widget-arm64')):
                    step = next(step for step in jobs[job]['steps'] if step.get('uses', '').startswith(action))
                    value = step['with'][field].replace('${{ inputs.image }}', image)
                    if field.startswith('cache-'):
                        value = re.search(r'(?:^|,)scope=([^,]+)', value).group(1)
                    values.append(value)
                self.assertEqual(len(set(values)), len(values), values)

    def test_release_has_no_unused_notes_file_input(self):
        release = yaml.safe_load((WORKFLOWS / 'release.yaml').read_text())
        self.assertNotIn('notes-file', release['on']['workflow_call']['inputs'])
        step = next(step for step in release['jobs']['build']['steps'] if step.get('id') == 'manifest')
        self.assertNotIn('NOTES_FILE', step.get('env', {}))
        self.assertNotIn('NOTES_FILE', step.get('run', ''))

    def test_image_qemu_defaults_to_false(self):
        image = yaml.safe_load((WORKFLOWS / 'image.yaml').read_text())
        self.assertEqual(image['on']['workflow_call']['inputs']['qemu'], {'type': 'boolean', 'default': False})

    def test_no_bare_always_in_generated_workflows(self):
        for path in sorted(WORKFLOWS.glob('*.yaml')):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r'\balways\(\)', line):
                    self.assertIn('!cancelled()', line, f'{path.name}:{number} uses always() without !cancelled()')

    def test_every_external_uses_has_version_comment(self):
        for path in sorted(WORKFLOWS.glob('*.yaml')):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                match = re.search(r'\buses: (\S+)(.*)$', line)
                if match and not match.group(1).startswith('./'):
                    self.assertRegex(match.group(2), r'^ # v\d+\.\d+\.\d+$', f'{path.name}:{number} uses lacks version comment')


class GoChecksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((WORKFLOWS / 'go-checks.yaml').read_text())
        cls.inputs = cls.workflow['on']['workflow_call']['inputs']
        cls.jobs = cls.workflow['jobs']
        cls.steps = cls.jobs['go']['steps']

    def test_pilot_steps_keep_their_order(self):
        names = [step.get('name') or step['uses'].split('@')[0] for step in self.steps]
        self.assertEqual(names, [
            'actions/checkout', 'actions/setup-node', 'actions/setup-go', 'goreleaser/goreleaser-action',
            'Prepare build inputs', 'Tidy', 'Check for reachable Go vulnerabilities',
            'golangci/golangci-lint-action', 'Lint', 'Fail if there are any changes', 'Test', 'Build snapshot',
        ])
        commands = [step['run'] for step in self.steps if 'run' in step]
        self.assertIn('make tidy', commands)
        self.assertIn('make lint', commands)
        self.assertIn('git diff --exit-code', commands)
        self.assertIn('make test', commands)
        self.assertTrue(any(re.fullmatch(r'go run golang\.org/x/vuln/cmd/govulncheck@v[\d.]+ \./\.\.\.', command)
                            for command in commands))
        self.assertEqual(self.inputs['goreleaser-id']['default'], 'all')
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        for job in self.jobs.values():
            checkout_options = job['steps'][0]['with']
            self.assertEqual(checkout_options, {'ref': '${{ github.sha }}', 'fetch-depth': 0,
                                               'persist-credentials': False})

    def test_caller_options_reach_the_tools(self):
        node = self.steps[1]
        self.assertEqual(node['if'], "${{ inputs.node-version != '' }}")
        self.assertEqual(node['with']['node-version'], '${{ inputs.node-version }}')
        self.assertEqual(node['with']['cache-dependency-path'], '${{ inputs.node-cache-dependency-path }}')
        self.assertEqual(self.steps[2]['with']['go-version-file'], 'go.mod')
        self.assertEqual(self.steps[4]['env']['PREPARE'], '${{ inputs.prepare-command }}')
        self.assertEqual(self.steps[7]['with']['version'], '${{ inputs.lint-version }}')
        self.assertIn("format('--build-tags={0}', inputs.build-tags)", self.steps[7]['with']['args'])
        self.assertIn("format('-tags={0}', inputs.build-tags)", self.jobs['go']['env']['GOFLAGS'])

    def test_optional_jobs_are_opt_in_and_do_not_publish(self):
        for name, workflow_input in [('chart', 'chart-path'), ('container', 'container-context')]:
            with self.subTest(job=name):
                self.assertEqual(self.inputs[workflow_input], {'type': 'string', 'default': ''})
                self.assertEqual(self.jobs[name]['if'], "${{ inputs." + workflow_input + " != '' }}")
        self.assertEqual(self.jobs['chart']['steps'][-1]['env']['CHART_PATH'], '${{ inputs.chart-path }}')
        container_options = self.jobs['container']['steps'][-1]['with']
        self.assertEqual(container_options, {'context': '${{ inputs.container-context }}', 'push': False})

    def test_snapshot_command_preserves_single_target_build(self):
        snapshot = self.steps[-1]
        self.assertEqual(snapshot['if'], self.steps[3]['if'])
        self.assertEqual(snapshot['if'], "${{ inputs.goreleaser-id != '' }}")
        self.assertEqual(snapshot['env']['GORELEASER_ID'], '${{ inputs.goreleaser-id }}')
        result = subprocess.run(
            ['bash', '-ec', 'goreleaser() { printf "%s\\n" "$@"; }\n' + snapshot['run']],
            env={'GORELEASER_ID': 'app'}, check=True, capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.splitlines(),
                         ['build', '--clean', '--snapshot', '--single-target', '--id', 'app', '--skip=before'])


class ImageBuildTests(unittest.TestCase):
    def test_smoke_copy_uses_native_platform_when_goarch_is_set(self):
        jobs = yaml.safe_load((WORKFLOWS / 'image.yaml').read_text())['jobs']
        stubs = '''sudo() { :; }
go() { printf '%s\\n' "$GOARCH"; }
skopeo() { printf '%s\\n' "$@" > "$COPY_ARGS"; }
'''
        for job in ('build', 'build-arm'):
            with self.subTest(job=job), tempfile.TemporaryDirectory() as directory:
                step = next(step for step in jobs[job]['steps'] if step.get('name', '').startswith('Smoke-test'))
                copy_args = Path(directory) / 'copy-args'
                env = {'PATH': os.environ['PATH'], 'GOARCH': 's390x', 'RUNNER_TEMP': directory,
                       'COPY_ARGS': str(copy_args), 'SMOKE': 'test -s "$COPY_ARGS"'}
                result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', stubs + step['run']],
                                        cwd=directory, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(copy_args.read_text().splitlines(),
                                 ['copy', f'oci-archive:{directory}/image.tar',
                                  'docker-daemon:local/publishing-smoke:latest'])


class ReleaseBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        release = yaml.safe_load((WORKFLOWS / 'release.yaml').read_text())
        cls.steps = release['jobs']['build']['steps']

    def test_goreleaser_uses_the_workflow_tag(self):
        step = next(step for step in self.steps if step.get('run', '').startswith('goreleaser release '))
        self.assertEqual(step.get('env', {}).get('GORELEASER_CURRENT_TAG'), '${{ github.ref_name }}')

    @unittest.skipUnless(shutil.which('helm'), 'helm is required for chart build tests')
    def test_chart_lint_checks_the_packaged_release_metadata(self):
        step = next(step for step in self.steps if 'helm package ' in step.get('run', ''))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart = root / 'charts/widget'
            (chart / 'templates').mkdir(parents=True)
            (chart / 'Chart.yaml').write_text(
                'apiVersion: v2\nname: widget\nversion: 0.1.0\nappVersion: "0.1.0"\n')
            (chart / 'templates/settings.yaml').write_text('''apiVersion: v1
kind: ConfigMap
metadata:
  name: widget
data:
  settings: {{ if and (eq .Chart.Version "2.0.0") (eq .Chart.AppVersion "2.0.0") }}[unclosed{{ else }}ok{{ end }}
''')
            env = {'PATH': os.environ['PATH'], 'CHART_PATH': 'charts/widget', 'CHART_NAME': 'widget',
                   'VERSION': '2.0.0', 'HELM_CONFIG_HOME': str(root / 'config'),
                   'HELM_CACHE_HOME': str(root / 'cache'), 'KUBECONFIG': str(root / 'kubeconfig')}
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']], cwd=root, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('templates/settings.yaml: unable to parse YAML', result.stdout)
        self.assertEqual(step.get('env', {}).get('CHART_NAME'), '${{ inputs.chart-name }}')

    def test_manifest_includes_goreleaser_archive_formats(self):
        step = next(step for step in self.steps if step.get('id') == 'manifest')
        archives = [
            'widget.tar.gz', 'widget.tgz', 'widget.zip', 'widget.tar.xz', 'widget.txz',
            'widget.tar.zst', 'widget.tzst', 'widget.tar', 'widget.gz', 'widget.xz',
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / 'dist'
            dist.mkdir()
            for name in archives:
                (dist / name).write_bytes(name.encode())
            (dist / 'artifacts.json').write_text('[]')
            output = root / 'output'
            subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']], cwd=root, check=True,
                           env={'PATH': os.environ['PATH'], 'GITHUB_OUTPUT': str(output)})
            staged = root / 'release-assets'
            manifest_data = (staged / 'manifest.json').read_bytes()
            manifest = json.loads(manifest_data)
            self.assertEqual(set(manifest), {*archives, 'SHA256SUMS'})
            for name in archives:
                self.assertEqual((staged / name).read_bytes(), name.encode())
                self.assertEqual(manifest[name], hashlib.sha256(name.encode()).hexdigest())
            self.assertEqual(output.read_text(), f'sha256={hashlib.sha256(manifest_data).hexdigest()}\n')

    def test_manifest_includes_goreleaser_sbom_outputs(self):
        step = next(step for step in self.steps if step.get('id') == 'manifest')
        sboms = {'widget_2.0.0_linux_amd64.tar.gz.sbom.json': b'{"bomFormat":"CycloneDX"}',
                 'widget-inventory.json': b'{"spdxVersion":"SPDX-2.3"}'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / 'dist'
            dist.mkdir()
            for name, data in sboms.items():
                (dist / name).write_bytes(data)
            (dist / 'metadata.json').write_text('{"version":"2.0.0"}')
            (dist / 'unlisted.sbom.json').write_text('{}')
            artifacts = [{'name': name, 'path': f'dist/{name}', 'type': 'SBOM'} for name in sboms]
            artifacts.append({'name': 'metadata.json', 'path': 'dist/metadata.json', 'type': 'Metadata'})
            (dist / 'artifacts.json').write_text(json.dumps(artifacts))
            output = root / 'output'
            subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']], cwd=root, check=True,
                           env={'PATH': os.environ['PATH'], 'GITHUB_OUTPUT': str(output), 'BINARIES': 'true'})
            staged = root / 'release-assets'
            manifest = json.loads((staged / 'manifest.json').read_text())
            self.assertEqual(set(manifest), {*sboms, 'SHA256SUMS'})
            checksums = (staged / 'SHA256SUMS').read_text().splitlines()
            for name, data in sboms.items():
                digest = hashlib.sha256(data).hexdigest()
                self.assertEqual((staged / name).read_bytes(), data)
                self.assertEqual(manifest[name], digest)
                self.assertIn(f'{digest}  {name}', checksums)
        self.assertEqual(step.get('env', {}).get('BINARIES'), '${{ inputs.binaries }}')

    def test_manifest_rejects_sbom_output_outside_dist(self):
        step = next(step for step in self.steps if step.get('id') == 'manifest')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / 'dist'
            dist.mkdir()
            (root / 'inventory.json').write_text('{}')
            (dist / 'inventory.json').symlink_to('../inventory.json')
            (dist / 'artifacts.json').write_text(
                '[{"name":"inventory.json","path":"dist/inventory.json","type":"SBOM"}]')
            env = {'PATH': os.environ['PATH'], 'GITHUB_OUTPUT': str(root / 'output'), 'BINARIES': 'true'}
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']], cwd=root, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn('SBOM is outside dist', result.stdout)
            self.assertFalse((root / 'release-assets/inventory.json').exists())


if __name__ == '__main__':
    unittest.main()
