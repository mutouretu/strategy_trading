from __future__ import annotations

import unittest
from copy import deepcopy

from experiment_system import (
    CodeRevision,
    ExperimentConfigError,
    ExperimentValidationError,
    canonical_json,
    parse_experiment_spec,
    plan_experiment,
)

from experiment_test_support import (
    experiment_document,
    registry_with_test_provider,
)


class ExperimentHashingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry, _ = registry_with_test_provider()

    def plan(
        self,
        document,
        *,
        commit: str = "a" * 40,
        tag: str | None = None,
    ):
        return plan_experiment(
            parse_experiment_spec(document),
            self.registry,
            code_revisions={
                "market_simulator": CodeRevision(
                    commit=commit,
                    tag=tag,
                )
            },
        )

    def test_canonical_json_sorts_mapping_keys_without_ascii_escaping(
        self,
    ) -> None:
        self.assertEqual(
            canonical_json({"z": 1, "a": "中文"}),
            '{"a":"中文","z":1}',
        )

    def test_human_labels_metadata_and_output_do_not_change_run_identity(
        self,
    ) -> None:
        original = experiment_document()
        relabelled = deepcopy(original)
        relabelled["experiment_id"] = "renamed-experiment"
        relabelled["metadata"] = {"purpose": "different"}
        relabelled["output"]["root"] = "another-directory"
        relabelled["scenario_groups"][0]["key"] = "renamed-group"
        relabelled["scenario_groups"][0]["markets"][0]["key"] = (
            "renamed-market"
        )

        first = self.plan(original)
        second = self.plan(relabelled)

        self.assertEqual(
            first.runs[0].configuration_hash,
            second.runs[0].configuration_hash,
        )
        self.assertEqual(first.runs[0].run_id, second.runs[0].run_id)

    def test_seed_changes_run_hash_but_not_scenario_id(self) -> None:
        plan = self.plan(experiment_document())
        seed_42, seed_43 = plan.runs[:2]

        self.assertEqual(
            seed_42.scenario.scenario_id,
            seed_43.scenario.scenario_id,
        )
        self.assertNotEqual(
            seed_42.configuration_hash,
            seed_43.configuration_hash,
        )
        self.assertNotEqual(seed_42.run_id, seed_43.run_id)

    def test_code_commit_changes_run_id_not_configuration_hash(self) -> None:
        first = self.plan(experiment_document(), commit="a" * 40)
        second = self.plan(experiment_document(), commit="b" * 40)

        self.assertEqual(
            first.runs[0].configuration_hash,
            second.runs[0].configuration_hash,
        )
        self.assertNotEqual(first.runs[0].run_id, second.runs[0].run_id)

    def test_tag_is_a_human_alias_and_does_not_change_run_id(self) -> None:
        untagged = self.plan(experiment_document())
        tagged = self.plan(
            experiment_document(),
            tag="baseline/grid-research-v1",
        )

        self.assertEqual(untagged.runs[0].run_id, tagged.runs[0].run_id)

    def test_dirty_revision_requires_a_content_fingerprint(self) -> None:
        with self.assertRaisesRegex(
            ExperimentConfigError,
            "dirty_fingerprint",
        ):
            CodeRevision(commit="a" * 40, dirty=True)

        revision = CodeRevision(
            commit="a" * 40,
            dirty=True,
            dirty_fingerprint="diff-sha256",
        )
        self.assertTrue(revision.dirty)

    def test_plan_requires_typed_code_revisions(self) -> None:
        with self.assertRaisesRegex(
            ExperimentValidationError,
            "at least one code revision",
        ):
            plan_experiment(
                parse_experiment_spec(experiment_document()),
                self.registry,
                code_revisions={},
            )

        with self.assertRaisesRegex(
            ExperimentValidationError,
            "must be CodeRevision",
        ):
            plan_experiment(
                parse_experiment_spec(experiment_document()),
                self.registry,
                code_revisions={"market_simulator": "not-a-revision"},
            )


if __name__ == "__main__":
    unittest.main()
