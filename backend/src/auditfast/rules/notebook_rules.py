"""Notebook rules — Area 3 (Spark Notebook Quality).

Best-practice and anti-pattern level only. Deep code quality (duplicate imports,
monolithic cells, display() in production paths) stays deep-dive/manual per
fabric-well-architected-auditor.md section 8 — Core does not pretend to review code.
"""

from __future__ import annotations

import re

from auditfast.config import Settings
from auditfast.inspectors.base import ArtifactEvidence, EvidenceBundle
from auditfast.rules.outcome import RuleOutcome
from auditfast.rules.pipeline_rules import SECRET_PATTERNS

# Fabric marks the parameters cell with a tag; the .py export marks it with a comment.
_PARAMETER_MARKERS = (
    "parameters",
    "# parameters",
    "%%configure",
)

_ABSOLUTE_PATH = re.compile(
    r"(abfss://[^\"'\s]+)|(wasbs://[^\"'\s]+)|(/lakehouse/default/Files/[^\"'\s]+)|"
    r"\b[A-Za-z]:\\[^\"'\s]+|(\\\\[A-Za-z0-9_.-]+\\[^\"'\s]+)",
    re.IGNORECASE,
)

_TIMEOUT_MARKERS = re.compile(
    r"(spark\.\w*timeout)|(\"?timeoutPerCellInSeconds\"?)|(sessionTimeout)|"
    r"(%%configure)|(spark\.databricks\.session\.timeout)|(livy\.server\.session\.timeout)",
    re.IGNORECASE,
)


def notebook_source(artifact: ArtifactEvidence) -> str:
    """Concatenate every code cell of a notebook definition into one searchable string."""
    if not artifact.definition:
        return ""

    chunks: list[str] = []
    for path, content in artifact.definition.items():
        if isinstance(content, str):
            chunks.append(content)
            continue
        if isinstance(content, dict) and (path.endswith(".ipynb") or "cells" in content):
            for cell in content.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                source = cell.get("source")
                text = "".join(source) if isinstance(source, list) else str(source or "")
                tags = (cell.get("metadata") or {}).get("tags") or []
                if tags:
                    chunks.append(f"# tags: {','.join(str(t) for t in tags)}")
                chunks.append(text)
    return "\n".join(chunks)


def notebook_cell_tags(artifact: ArtifactEvidence) -> set[str]:
    tags: set[str] = set()
    if not artifact.definition:
        return tags
    for _path, content in artifact.definition.items():
        if isinstance(content, dict):
            for cell in content.get("cells") or []:
                if isinstance(cell, dict):
                    for tag in (cell.get("metadata") or {}).get("tags") or []:
                        tags.add(str(tag).lower())
    return tags


def _requires_definitions(
    bundle: EvidenceBundle, artifacts: list[ArtifactEvidence]
) -> RuleOutcome | None:
    if not artifacts:
        return RuleOutcome.not_applicable("The workspace contains no notebooks.")
    if not bundle.definitions_enabled:
        return RuleOutcome.unavailable(
            "Definition reads are disabled. Set AUDITFAST_ENABLE_DEFINITION_READS=true and "
            "grant the Item.ReadWrite.All delegated scope that Fabric's getDefinition API "
            "requires, then re-run. The guardrail still blocks every write."
        )
    if all(a.definition_error for a in artifacts):
        first = next(a.definition_error for a in artifacts if a.definition_error)
        return RuleOutcome.unavailable(f"No notebook definition could be read: {first}")
    return None


def _with_definitions(artifacts: list[ArtifactEvidence]) -> list[ArtifactEvidence]:
    return [a for a in artifacts if a.has_definition]


# -- rules -------------------------------------------------------------------


def notebook_naming_convention(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    notebooks = bundle.of_type("notebook")
    if not notebooks:
        return RuleOutcome.not_applicable("The workspace contains no notebooks.")

    try:
        pattern = re.compile(settings.notebook_name_pattern)
    except re.error as exc:
        return RuleOutcome.unavailable(f"Invalid notebook name pattern configured: {exc}")

    passing = [n.name for n in notebooks if pattern.match(n.name)]
    failing = [n.name for n in notebooks if not pattern.match(n.name)]
    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(notebooks)} notebooks match "
        f"{settings.notebook_name_pattern!r}.",
    )


def notebook_parameterized(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    notebooks = bundle.of_type("notebook")
    if (guard := _requires_definitions(bundle, notebooks)) is not None:
        return guard

    passing, failing = [], []
    for notebook in _with_definitions(notebooks):
        tags = notebook_cell_tags(notebook)
        source = notebook_source(notebook).lower()
        has_parameters = "parameters" in tags or any(
            marker in source for marker in _PARAMETER_MARKERS
        )
        (passing if has_parameters else failing).append(notebook.name)

    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(passing) + len(failing)} notebooks declare a parameters cell.",
    )


def notebook_no_hardcoded_secrets(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    notebooks = bundle.of_type("notebook")
    if (guard := _requires_definitions(bundle, notebooks)) is not None:
        return guard

    passing, failing = [], []
    for notebook in _with_definitions(notebooks):
        source = notebook_source(notebook)
        secrets = SECRET_PATTERNS.search(source)
        paths = _ABSOLUTE_PATH.findall(source)
        if secrets or paths:
            reason = []
            if secrets:
                reason.append("credential-shaped literal")
            if paths:
                reason.append(f"{len(paths)} hardcoded path(s)")
            failing.append(f"{notebook.name} ({', '.join(reason)})")
        else:
            passing.append(notebook.name)

    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(failing)} of {len(passing) + len(failing)} notebooks contain a secret or "
        "hardcoded environment-specific path.",
    )


def notebook_timeout_configured(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    notebooks = bundle.of_type("notebook")
    if (guard := _requires_definitions(bundle, notebooks)) is not None:
        return guard

    passing, failing = [], []
    for notebook in _with_definitions(notebooks):
        source = notebook_source(notebook)
        (passing if _TIMEOUT_MARKERS.search(source) else failing).append(notebook.name)

    return RuleOutcome.coverage_result(
        passing,
        failing,
        f"{len(passing)} of {len(passing) + len(failing)} notebooks declare a session or "
        "execution timeout. Timeouts set on the Environment rather than in the notebook "
        "are not visible to this check — confirm manually before treating a fail as final.",
    )
