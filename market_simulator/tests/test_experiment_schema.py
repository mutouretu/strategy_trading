from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiment_system import (
    ExperimentConfigError,
    RetentionClass,
    load_experiment_spec,
    parse_experiment_spec,
)

from experiment_test_support import experiment_document


class ExperimentSchemaTests(unittest.TestCase):
    def test_parses_strict_v1_document_and_preserves_decimal_strings(
        self,
    ) -> None:
        spec = parse_experiment_spec(experiment_document())

        self.assertEqual(spec.experiment_id, "grid-research")
        self.assertEqual(len(spec.scenario_groups), 2)
        self.assertEqual(spec.seeds, (42, 43))
        self.assertEqual(
            spec.scenario_groups[0]
            .executions[0]
            .parameters["maker_fee_rate"],
            "0.0002",
        )
        self.assertEqual(
            spec.output.default_retention_class,
            RetentionClass.STANDARD,
        )

    def test_json_floats_are_rejected_in_parameters_and_metadata(self) -> None:
        for mutate in (
            lambda document: document["scenario_groups"][0]["markets"][0][
                "parameters"
            ].update({"annual_volatility": 0.55}),
            lambda document: document["metadata"].update({"score": 1.5}),
        ):
            with self.subTest(mutate=mutate):
                document = experiment_document()
                mutate(document)
                with self.assertRaisesRegex(
                    ExperimentConfigError,
                    "decimal string",
                ):
                    parse_experiment_spec(document)

    def test_unknown_fields_and_duplicate_seeds_are_rejected(self) -> None:
        document = experiment_document()
        document["unexpected"] = True
        with self.assertRaisesRegex(
            ExperimentConfigError,
            "unknown fields",
        ):
            parse_experiment_spec(document)

        document = experiment_document()
        document["seeds"] = [42, 42]
        with self.assertRaisesRegex(
            ExperimentConfigError,
            "duplicates",
        ):
            parse_experiment_spec(document)

    def test_empty_component_lists_and_duplicate_axis_paths_are_rejected(
        self,
    ) -> None:
        document = experiment_document()
        document["scenario_groups"][0]["accounts"] = []
        with self.assertRaisesRegex(ExperimentConfigError, "requires accounts"):
            parse_experiment_spec(document)

        document = experiment_document()
        axis = document["scenario_groups"][0]["parameter_axes"][0]
        document["scenario_groups"][0]["parameter_axes"].append(dict(axis))
        with self.assertRaisesRegex(
            ExperimentConfigError,
            "duplicate parameter paths",
        ):
            parse_experiment_spec(document)

    def test_load_reports_json_location_and_reads_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            valid_path = Path(directory) / "experiment.json"
            valid_path.write_text(
                json.dumps(experiment_document()),
                encoding="utf-8",
            )
            self.assertEqual(
                load_experiment_spec(valid_path).experiment_id,
                "grid-research",
            )

            invalid_path = Path(directory) / "invalid.json"
            invalid_path.write_text('{"broken":', encoding="utf-8")
            with self.assertRaisesRegex(
                ExperimentConfigError,
                r"invalid\.json:1:11",
            ):
                load_experiment_spec(invalid_path)


if __name__ == "__main__":
    unittest.main()
