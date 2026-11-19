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
        for name in ('decide', 'image', 'release'):
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


if __name__ == '__main__':
    unittest.main()
