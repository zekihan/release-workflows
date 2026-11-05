import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('policy', Path(__file__).parents[1] / 'scripts/policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class PolicyTests(unittest.TestCase):
    def test_stable_versions_are_numeric_and_reject_prereleases(self):
        self.assertTrue(policy.latest_decision('1.9.9', '1.10.0'))
        self.assertFalse(policy.latest_decision('2.0.0', '1.10.0'))
        self.assertFalse(policy.latest_decision('1.0.0', '1.0.0'))
        for value in ('v1.0.0', '1.0.0-rc.1', '01.0.0', '1.2', '1.2.3\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.stable_version(value)

    def test_composite_upstream_versions(self):
        self.assertEqual(policy.release_identity('18.4-vectorchord1.1.1', 2), '18.4-vectorchord1.1.1-r2')
        for revision in (0, -1, '1.5', '01'):
            with self.assertRaises(ValueError):
                policy.release_identity('3.27.1', revision)

    def test_failure_suppression_and_manual_recovery(self):
        base = dict(manual=False, force=False, failed=True, source_matches=False, mirrors_match=False)
        self.assertEqual(policy.build_decision(**base), 'skip-failure')
        self.assertEqual(policy.build_decision(**(base | {'manual': True})), 'build')
        self.assertEqual(policy.build_decision(**(base | {'failed': False})), 'build')

    def test_success_and_partial_publication(self):
        base = dict(manual=False, force=False, failed=False, source_matches=True, mirrors_match=True)
        self.assertEqual(policy.build_decision(**base), 'skip-success')
        self.assertEqual(policy.build_decision(**(base | {'mirrors_match': False})), 'build')
        self.assertEqual(policy.build_decision(**(base | {'manual': True, 'force': True})), 'build')
        with self.assertRaises(ValueError):
            policy.build_decision(**(base | {'force': True}))

    def test_failed_manual_rerun_does_not_hide_existing_success(self):
        self.assertEqual(policy.build_decision(manual=False, force=False, failed=True,
                                             source_matches=True, mirrors_match=True), 'skip-success')

    def test_immutable_conflicts(self):
        self.assertEqual(policy.immutable_decision(None, 'sha256:new'), 'publish')
        self.assertEqual(policy.immutable_decision('sha256:new', 'sha256:new'), 'skip')
        with self.assertRaises(ValueError):
            policy.immutable_decision('sha256:old', 'sha256:new')

    def test_stale_and_unrelated_runs(self):
        ancestor = lambda old, new: (old, new) == ('old', 'new')
        policy.assert_forward(None, 'new', ancestor)
        policy.assert_forward('new', 'new', ancestor)
        policy.assert_forward('old', 'new', ancestor)
        for current, candidate in [('new', 'old'), ('unrelated', 'new')]:
            with self.assertRaises(ValueError):
                policy.assert_forward(current, candidate, ancestor)

    def test_missing_platform_and_attestation_manifest(self):
        index = {'manifests': [{'platform': {'os': 'linux', 'architecture': 'amd64'}},
                               {'platform': {'os': 'unknown', 'architecture': 'unknown'}}]}
        policy.validate_platforms(index, 'linux/amd64')
        with self.assertRaises(ValueError):
            policy.validate_platforms(index, 'linux/amd64,linux/arm64')


if __name__ == '__main__':
    unittest.main()
