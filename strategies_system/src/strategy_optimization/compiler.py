"""Compile strategy Studies into the existing experiment system."""

from __future__ import annotations

from collections.abc import Mapping

from experiment_system import (
    ExperimentSpec,
    experiment_spec_to_document,
    sha256_document,
)
from metric_system import MetricRegistry, MetricValueType

from .errors import StudyConfigError
from .models import (
    CompiledStudy,
    DatasetRole,
    MetricSelector,
    StudyBundle,
)


def _validate_selector(
    selector: MetricSelector,
    registry: MetricRegistry,
) -> None:
    calculator = registry.calculator(
        selector.metric_set_id,
        selector.metric_set_version,
    )
    definition = calculator.metric_set.definition(selector.metric_key)
    if set(selector.dimensions) != set(definition.dimensions):
        raise StudyConfigError(
            f"metric selector {selector.metric_key!r} dimensions "
            f"{sorted(selector.dimensions)} do not match definition "
            f"{sorted(definition.dimensions)}"
        )
    generic_units = {
        "asset",
        "notional_asset",
        "settlement_asset",
        "quantity",
    }
    if (
        definition.unit_kind not in generic_units
        and selector.unit != definition.unit_kind
    ):
        raise StudyConfigError(
            f"metric selector {selector.metric_key!r} unit "
            f"{selector.unit!r} must be {definition.unit_kind!r}"
        )


def validate_objective_profile(
    bundle: StudyBundle,
    registry: MetricRegistry,
) -> None:
    profile = bundle.objective_profile
    if profile.valuation_asset.upper() != profile.valuation_asset:
        raise StudyConfigError("objective valuation_asset must be uppercase")
    for item in profile.objectives:
        _validate_selector(item.selector, registry)
        if item.selector.unit != profile.valuation_asset and item.selector.unit not in {
            "ratio",
            "count",
        }:
            raise StudyConfigError(
                f"objective {item.key!r} unit is inconsistent with "
                f"valuation_asset={profile.valuation_asset!r}"
            )
    for item in profile.eligibility_constraints:
        _validate_selector(item.selector, registry)
        definition = registry.calculator(
            item.selector.metric_set_id,
            item.selector.metric_set_version,
        ).metric_set.definition(item.selector.metric_key)
        if definition.value_type is MetricValueType.BOOLEAN and not isinstance(
            item.value, bool
        ):
            raise StudyConfigError(
                f"boolean constraint {item.key!r} requires a boolean value"
            )


def _component_strategy_types(bundle: StudyBundle) -> set[str]:
    return {
        component.type
        for group in bundle.experiment.scenario_groups
        for component in group.strategies
    }


def _component_market_keys(bundle: StudyBundle) -> set[str]:
    return {
        component.key
        for group in bundle.experiment.scenario_groups
        for component in group.markets
    }


def _validate_bundle(bundle: StudyBundle, registry: MetricRegistry) -> None:
    study = bundle.study
    profile = bundle.objective_profile
    strategy_types = _component_strategy_types(bundle)
    if study.strategy_family not in strategy_types:
        raise StudyConfigError(
            f"strategy_family {study.strategy_family!r} is not present in "
            "the ExperimentSpec"
        )
    missing_baselines = set(study.baseline_ids) - strategy_types
    if missing_baselines:
        raise StudyConfigError(
            f"baseline strategy types are not present in the ExperimentSpec: "
            f"{sorted(missing_baselines)}"
        )
    if profile.baseline_strategy_type not in study.baseline_ids:
        raise StudyConfigError(
            "objective profile baseline_strategy_type must be included in "
            "Study baseline_ids"
        )
    market_keys = _component_market_keys(bundle)
    markets_by_key = {
        component.key: component
        for group in bundle.experiment.scenario_groups
        for component in group.markets
    }
    for role in (DatasetRole.TRAIN, DatasetRole.VALIDATION):
        window = bundle.dataset_split.window(role)
        required_key = window.market_key
        if required_key not in market_keys:
            raise StudyConfigError(
                f"ExperimentSpec is missing {role.value} market "
                f"{required_key!r}"
            )
        if bundle.dataset_split.formal_ready:
            actual_content_hash = markets_by_key[
                required_key
            ].parameters.get("content_sha256")
            if actual_content_hash != window.content_sha256:
                raise StudyConfigError(
                    f"Experiment market {required_key!r} content_sha256 "
                    "does not match its CONTENT_LOCKED dataset window"
                )
    holdout_key = bundle.dataset_split.window(DatasetRole.HOLDOUT).market_key
    if holdout_key in market_keys:
        raise StudyConfigError(
            f"final HOLDOUT market {holdout_key!r} must not be present in "
            "a parameter-development Study"
        )
    for group in bundle.experiment.scenario_groups:
        for component in group.markets:
            instrument = component.parameters.get("instrument")
            if (
                instrument is not None
                and str(instrument) != bundle.dataset_split.instrument
            ):
                raise StudyConfigError(
                    f"market {component.key!r} instrument {instrument!r} "
                    f"does not match dataset instrument "
                    f"{bundle.dataset_split.instrument!r}"
                )
    validate_objective_profile(bundle, registry)


def _bundle_documents(bundle: StudyBundle) -> tuple[dict[str, object], dict[str, object]]:
    study_document = {
        "study": bundle.study.to_document(),
        "experiment": experiment_spec_to_document(bundle.experiment),
    }
    protocol_document = {
        "objective_profile": bundle.objective_profile.to_document(),
        "dataset_split": bundle.dataset_split.to_document(),
    }
    return study_document, protocol_document


def _metric_definition_bindings(
    bundle: StudyBundle,
    registry: MetricRegistry,
) -> list[dict[str, str]]:
    identities = {
        (
            item.selector.metric_set_id,
            item.selector.metric_set_version,
        )
        for item in (
            *bundle.objective_profile.objectives,
            *bundle.objective_profile.eligibility_constraints,
        )
    }
    return [
        {
            "metric_set_id": metric_set_id,
            "metric_set_version": version,
            "definition_hash": registry.calculator(
                metric_set_id,
                version,
            ).metric_set.definition_hash,
        }
        for metric_set_id, version in sorted(identities)
    ]


def compile_study(
    bundle: StudyBundle,
    *,
    metric_registry: MetricRegistry,
) -> CompiledStudy:
    """Validate research semantics and enrich an existing ExperimentSpec."""

    _validate_bundle(bundle, metric_registry)
    study_document, protocol_document = _bundle_documents(bundle)
    metric_bindings = _metric_definition_bindings(bundle, metric_registry)
    protocol_document["metric_definition_bindings"] = metric_bindings
    study_fingerprint = sha256_document(study_document)
    protocol_fingerprint = sha256_document(protocol_document)
    metadata: dict[str, object] = dict(bundle.experiment.metadata)
    if "strategy_study" in metadata:
        raise StudyConfigError(
            "ExperimentSpec metadata already contains strategy_study"
        )
    metadata["strategy_study"] = {
        "study_id": bundle.study.study_id,
        "study_fingerprint": study_fingerprint,
        "protocol_fingerprint": protocol_fingerprint,
        "objective_profile_id": bundle.objective_profile.profile_id,
        "dataset_split_id": bundle.dataset_split.split_id,
        "dataset_status": bundle.dataset_split.status.value,
        "formal_ready": bundle.dataset_split.formal_ready,
        "selection_policy": bundle.study.selection_policy,
        "metric_definition_bindings": metric_bindings,
    }
    template = bundle.experiment
    experiment = ExperimentSpec(
        experiment_id=template.experiment_id,
        scenario_groups=template.scenario_groups,
        seeds=template.seeds,
        description=template.description,
        schema_version=template.schema_version,
        output=template.output,
        controls=template.controls,
        metadata=metadata,
    )
    return CompiledStudy(
        bundle=bundle,
        experiment=experiment,
        study_fingerprint=study_fingerprint,
        protocol_fingerprint=protocol_fingerprint,
    )
