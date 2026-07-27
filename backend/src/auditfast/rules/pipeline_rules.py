"""Pipeline rules — Area 2 (Pipeline Design, Error Handling & Retry).

All of these read the pipeline definition JSON returned by getDefinition. When
definition reads are disabled the engine reports `evidence_unavailable` rather than
scoring on absent evidence.
"""

from __future__ import annotations

import json
import re
from typing import Any

from auditfast.config import Settings
from auditfast.inspectors.base import ArtifactEvidence, EvidenceBundle
from auditfast.rules.outcome import RuleOutcome

# Activities that reach an external system and therefore should carry a retry policy.
RETRYABLE_ACTIVITIES = {
    "Copy",
    "TridentNotebook",
    "SynapseNotebook",
    "SparkJobDefinition",
    "Lookup",
    "GetMetadata",
    "WebActivity",
    "Web",
    "SqlServerStoredProcedure",
    "Script",
    "Delete",
    "AzureFunctionActivity",
    "DatabricksNotebook",
    "ExecuteDataFlow",
}

# Activities that deliver a failure notification.
NOTIFICATION_ACTIVITIES = {"Office365Outlook", "Teams", "WebActivity", "Web", "Activator"}

CONTAINER_ACTIVITY_KEYS = (
    "activities",
    "ifTrueActivities",
    "ifFalseActivities",
    "defaultActivities",
)

# Hardcoded environment-specific literals that belong in parameters.
HARDCODED_LITERALS = re.compile(
    r"(abfss://[^\"'\s]+)|(https?://(?!schema\.|www\.w3\.)[^\"'\s]+)|"
    r"(\\\\[A-Za-z0-9_.-]+\\[^\"'\s]+)|"
    r"\b\d{1,3}(\.\d{1,3}){3}\b|"
    r"\b[A-Za-z]:\\[^\"'\s]+",
    re.IGNORECASE,
)

SECRET_PATTERNS = re.compile(
    r"(password\s*=)|(pwd\s*=)|(accountkey\s*=)|(sharedaccesssignature)|"
    r"(client[_-]?secret\s*[=:])|(api[_-]?key\s*[=:])|(bearer\s+[A-Za-z0-9._-]{20,})|"
    r"(sv=\d{4}-\d{2}-\d{2}&s[ip]g?=)|(-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


def pipeline_body(artifact: ArtifactEvidence) -> dict[str, Any] | None:
    """Pull the pipeline JSON out of the decoded definition parts."""
    if not artifact.definition:
        return None
    for path, content in artifact.definition.items():
        if path.endswith(".json") and isinstance(content, dict):
            if "activities" in content or "properties" in content:
                return content.get("properties", content) if "properties" in content else content
    return None


def iter_activities(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten activities, descending into ForEach/If/Switch/Until containers."""
    if not body:
        return []
    found: list[dict[str, Any]] = []

    def walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            found.append(node)
            type_props = node.get("typeProperties") or {}
            for key in CONTAINER_ACTIVITY_KEYS:
                walk(type_props.get(key))
            for case in type_props.get("cases") or []:
                if isinstance(case, dict):
                    walk(case.get("activities"))

    walk(body.get("activities"))
    return found


def _requires_definitions(
    bundle: EvidenceBundle, artifacts: list[ArtifactEvidence]
) -> RuleOutcome | None:
    """Shared guard: is there anything to evaluate, and do we have definitions?"""
    if not artifacts:
        return RuleOutcome.not_applicable("The workspace contains no pipelines.")
    if not bundle.definitions_enabled:
        return RuleOutcome.unavailable(
            "Definition reads are disabled. Set AUDITFAST_ENABLE_DEFINITION_READS=true and "
            "grant the Item.ReadWrite.All delegated scope that Fabric's getDefinition API "
            "requires, then re-run. The guardrail still blocks every write."
        )
    if all(a.definition_error for a in artifacts):
        first = next(a.definition_error for a in artifacts if a.definition_error)
        return RuleOutcome.unavailable(f"No pipeline definition could be read: {first}")
    return None


def _with_definitions(artifacts: list[ArtifactEvidence]) -> list[ArtifactEvidence]:
    return [a for a in artifacts if a.has_definition]


# -- rules -------------------------------------------------------------------


def pipeline_naming_convention(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    pipelines = bundle.of_type("pipeline")
    if not pipelines:
        return RuleOutcome.not_applicable("The workspace contains no pipelines.")

    try:
        pattern = re.compile(settings.pipeline_name_pattern)
    except re.error as exc:
        return RuleOutcome.unavailable(f"Invalid pipeline name pattern configured: {exc}")

    passing = [p.name for p in pipelines if pattern.match(p.name)]
    failing = [p.name for p in pipelines if not pattern.match(p.name)]
    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(pipelines)} pipelines match "
        f"{settings.pipeline_name_pattern!r}.",
    )


def pipeline_parameterized(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    pipelines = bundle.of_type("pipeline")
    if (guard := _requires_definitions(bundle, pipelines)) is not None:
        return guard

    passing, failing = [], []
    for pipeline in _with_definitions(pipelines):
        body = pipeline_body(pipeline)
        has_parameters = bool((body or {}).get("parameters"))
        serialized = json.dumps(body or {})
        literals = set(m.group(0) for m in HARDCODED_LITERALS.finditer(serialized))
        if has_parameters and not literals:
            passing.append(pipeline.name)
        else:
            reason = []
            if not has_parameters:
                reason.append("no parameters declared")
            if literals:
                sample = ", ".join(sorted(literals)[:3])
                reason.append(f"{len(literals)} hardcoded literal(s) e.g. {sample}")
            failing.append(f"{pipeline.name} ({'; '.join(reason)})")

    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(passing) + len(failing)} pipelines are parameterized with "
        "no environment-specific literals.",
    )


def pipeline_annotations(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    pipelines = bundle.of_type("pipeline")
    if (guard := _requires_definitions(bundle, pipelines)) is not None:
        return guard

    passing, failing = [], []
    for pipeline in _with_definitions(pipelines):
        activities = iter_activities(pipeline_body(pipeline))
        described = [a for a in activities if str(a.get("description", "")).strip()]
        activity_ratio = len(described) / len(activities) if activities else 1.0
        has_description = bool(pipeline.description.strip())

        if has_description and activity_ratio >= 0.8:
            passing.append(pipeline.name)
        else:
            reason = []
            if not has_description:
                reason.append("pipeline description empty")
            if activity_ratio < 0.8:
                reason.append(
                    f"{len(described)}/{len(activities)} activities described"
                )
            failing.append(f"{pipeline.name} ({'; '.join(reason)})")

    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(passing) + len(failing)} pipelines are documented at both "
        "pipeline and activity level.",
    )


def pipeline_retry_configured(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    pipelines = bundle.of_type("pipeline")
    if (guard := _requires_definitions(bundle, pipelines)) is not None:
        return guard

    passing, failing = [], []
    for pipeline in _with_definitions(pipelines):
        for activity in iter_activities(pipeline_body(pipeline)):
            if activity.get("type") not in RETRYABLE_ACTIVITIES:
                continue
            retry = (activity.get("policy") or {}).get("retry", 0)
            label = f"{pipeline.name} / {activity.get('name', '?')} [{activity.get('type')}]"
            try:
                retry_count = int(retry)
            except (TypeError, ValueError):
                retry_count = 0
            (passing if retry_count >= 1 else failing).append(label)

    if not passing and not failing:
        return RuleOutcome.not_applicable(
            "No externally-dependent activities were found in the inspected pipelines."
        )
    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(passing) + len(failing)} externally-dependent activities "
        "configure a retry policy.",
    )


def pipeline_retry_bounds(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    pipelines = bundle.of_type("pipeline")
    if (guard := _requires_definitions(bundle, pipelines)) is not None:
        return guard

    passing, failing = [], []
    for pipeline in _with_definitions(pipelines):
        for activity in iter_activities(pipeline_body(pipeline)):
            policy = activity.get("policy") or {}
            if "retry" not in policy:
                continue
            label = f"{pipeline.name} / {activity.get('name', '?')}"
            try:
                retry = int(policy.get("retry", 0))
                interval = int(policy.get("retryIntervalInSeconds", 0))
            except (TypeError, ValueError):
                failing.append(f"{label} (unparseable retry policy)")
                continue
            if 1 <= retry <= 5 and interval >= 10:
                passing.append(label)
            else:
                failing.append(f"{label} (retry={retry}, interval={interval}s)")

    if not passing and not failing:
        return RuleOutcome.not_applicable("No activity configures a retry policy.")
    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(passing) + len(failing)} configured retry policies sit "
        "within 1-5 attempts at a >=10s interval.",
    )


def pipeline_failure_paths(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    pipelines = bundle.of_type("pipeline")
    if (guard := _requires_definitions(bundle, pipelines)) is not None:
        return guard

    passing, failing = [], []
    for pipeline in _with_definitions(pipelines):
        activities = iter_activities(pipeline_body(pipeline))
        if len(activities) < 2:
            continue
        has_failure_edge = any(
            str(condition).lower() in {"failed", "skipped", "completed"}
            for activity in activities
            for dependency in activity.get("dependsOn") or []
            for condition in (dependency.get("dependencyConditions") or [])
        )
        (passing if has_failure_edge else failing).append(pipeline.name)

    if not passing and not failing:
        return RuleOutcome.not_applicable("No multi-activity pipelines were found.")
    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(passing) + len(failing)} multi-activity pipelines define "
        "an explicit on-failure path.",
    )


def pipeline_failure_notification(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    pipelines = bundle.of_type("pipeline")
    if (guard := _requires_definitions(bundle, pipelines)) is not None:
        return guard

    passing, failing = [], []
    for pipeline in _with_definitions(pipelines):
        activities = iter_activities(pipeline_body(pipeline))
        notifiers = {
            activity.get("name")
            for activity in activities
            if activity.get("type") in NOTIFICATION_ACTIVITIES
        }
        on_failure_notifier = any(
            activity.get("name") in notifiers
            and any(
                str(condition).lower() in {"failed", "skipped"}
                for dependency in activity.get("dependsOn") or []
                for condition in (dependency.get("dependencyConditions") or [])
            )
            for activity in activities
        )
        (passing if on_failure_notifier else failing).append(pipeline.name)

    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(passing) + len(failing)} pipelines notify a human when a "
        "failure path is taken.",
    )


def pipeline_no_hardcoded_secrets(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    """Not in the MVP catalog as its own item, but reused by the notebook secret scan."""
    pipelines = bundle.of_type("pipeline")
    if (guard := _requires_definitions(bundle, pipelines)) is not None:
        return guard

    passing, failing = [], []
    for pipeline in _with_definitions(pipelines):
        serialized = json.dumps(pipeline_body(pipeline) or {})
        if SECRET_PATTERNS.search(serialized):
            failing.append(pipeline.name)
        else:
            passing.append(pipeline.name)

    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(failing)} of {len(passing) + len(failing)} pipelines contain a "
        "credential-shaped literal.",
    )
