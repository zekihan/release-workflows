from pathlib import Path
import re
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


if __name__ == '__main__':
    unittest.main()
