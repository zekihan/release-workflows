from pathlib import Path
import re
import unittest

WORKFLOWS = Path(__file__).parents[1] / '.github/workflows'


class WorkflowConditionTests(unittest.TestCase):
    def test_no_bare_always_in_generated_workflows(self):
        for path in sorted(WORKFLOWS.glob('*.yaml')):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r'\balways\(\)', line):
                    self.assertIn('!cancelled()', line, f'{path.name}:{number} uses always() without !cancelled()')


if __name__ == '__main__':
    unittest.main()
