from __future__ import annotations

import json
import unittest

from experiment_system import (
    CodeRevision,
    ExperimentValidationError,
    ProviderRegistry,
    UnknownProviderError,
    parse_experiment_spec,
    plan_experiment,
    plan_to_document,
    validate_experiment,
)
from experiment_system.json_pointer import replace_pointer

from experiment_test_support import (
    experiment_document,
    registry_with_test_provider,
)


class ExperimentExpansionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry, self.provider = registry_with_test_provider()
        self.revisions = {
            "market_simulator": CodeRevision(commit="a" * 40)
        }

    def test_scenario_groups_expand_independently_in_stable_order(self) -> None:
        spec = parse_experiment_spec(experiment_document())

        plan = plan_experiment(
            spec,
            self.registry,
            code_revisions=self.revisions,
        )

        self.assertEqual(plan.scenario_count, 5)
        self.assertEqual(plan.run_count, 10)
        self.assertEqual(
            [
                (
                    run.configuration.group_key,
                    run.configuration.market.key,
                    run.configuration.strategy.parameters.get(
                        "order_quantity"
                    ),
                    run.seed,
                )
                for run in plan.runs
            ],
            [
                ("coinm-grid", "market-a", "1", 42),
                ("coinm-grid", "market-a", "1", 43),
                ("coinm-grid", "market-a", "2", 42),
                ("coinm-grid", "market-a", "2", 43),
                ("coinm-grid", "market-b", "1", 42),
                ("coinm-grid", "market-b", "1", 43),
                ("coinm-grid", "market-b", "2", 42),
                ("coinm-grid", "market-b", "2", 43),
                ("linear-probe", "market-c", None, 42),
                ("linear-probe", "market-c", None, 43),
            ],
        )
        self.assertTrue(
            all(
                run.configuration.execution.parameters[
                    "resolved_default"
                ]
                == "yes"
                for run in plan.runs
            )
        )
        self.assertEqual(self.provider.prepare_calls, 0)

    def test_validate_reports_counts_without_code_revisions(self) -> None:
        report = validate_experiment(
            parse_experiment_spec(experiment_document()),
            self.registry,
        )

        self.assertEqual(report.scenario_count, 5)
        self.assertEqual(report.run_count, 10)
        self.assertEqual(report.provider_ids, ("test-simulation/v1",))

    def test_plan_document_is_compact_and_preserves_axis_values(self) -> None:
        plan = plan_experiment(
            parse_experiment_spec(experiment_document()),
            self.registry,
            code_revisions=self.revisions,
        )

        document = plan_to_document(plan)

        self.assertEqual(document["run_count"], 10)
        self.assertEqual(
            document["runs"][0]["parameter_values"],
            {"/strategy/parameters/order_quantity": "1"},
        )
        self.assertEqual(
            document["runs"][8]["parameter_values"],
            {},
        )
        json.dumps(document)

    def test_max_runs_is_checked_before_provider_resolution(self) -> None:
        document = experiment_document()
        document["controls"]["max_runs"] = 9

        with self.assertRaisesRegex(
            ExperimentValidationError,
            "expands to 10 runs",
        ):
            validate_experiment(
                parse_experiment_spec(document),
                self.registry,
            )

        self.assertEqual(self.provider.prepare_calls, 0)

    def test_unknown_provider_and_incompatible_components_fail_preflight(
        self,
    ) -> None:
        with self.assertRaises(UnknownProviderError):
            validate_experiment(
                parse_experiment_spec(experiment_document()),
                ProviderRegistry(),
            )

        document = experiment_document()
        document["scenario_groups"][0]["strategies"][0]["parameters"][
            "instrument"
        ] = "ETHUSD_PERP"
        with self.assertRaisesRegex(
            ExperimentValidationError,
            "instruments must match",
        ):
            validate_experiment(
                parse_experiment_spec(document),
                self.registry,
            )

    def test_duplicate_resolved_scenarios_are_rejected(self) -> None:
        document = experiment_document()
        duplicate = document["scenario_groups"][0]["markets"][0].copy()
        duplicate["key"] = "market-duplicate-label"
        document["scenario_groups"][0]["markets"] = [
            document["scenario_groups"][0]["markets"][0],
            duplicate,
        ]

        with self.assertRaisesRegex(
            ExperimentValidationError,
            "duplicate resolved scenario",
        ):
            validate_experiment(
                parse_experiment_spec(document),
                self.registry,
            )

    def test_json_pointer_replaces_nested_list_and_decodes_escapes(self) -> None:
        document = {
            "market": {
                "parameters": {
                    "anchors": [["2026-01-01", "60000"]],
                    "a/b": {"~price": "1"},
                }
            }
        }

        updated = replace_pointer(
            document,
            "/market/parameters/anchors/0/1",
            "65000",
        )
        updated = replace_pointer(
            updated,
            "/market/parameters/a~1b/~0price",
            "2",
        )

        self.assertEqual(
            updated["market"]["parameters"]["anchors"][0][1],
            "65000",
        )
        self.assertEqual(
            updated["market"]["parameters"]["a/b"]["~price"],
            "2",
        )
        self.assertEqual(
            document["market"]["parameters"]["anchors"][0][1],
            "60000",
        )

    def test_parameter_axis_must_target_an_existing_parameter(self) -> None:
        document = experiment_document()
        document["scenario_groups"][0]["parameter_axes"][0]["path"] = (
            "/strategy/parameters/typo"
        )
        with self.assertRaisesRegex(
            ExperimentValidationError,
            "does not exist",
        ):
            validate_experiment(
                parse_experiment_spec(document),
                self.registry,
            )


if __name__ == "__main__":
    unittest.main()
