"""Deterministic rule engine — pure functions over collected evidence."""

from auditfast_mcp.rules.engine import RULES, RuleOutcome, evaluate_check, run_rules

__all__ = ["RULES", "RuleOutcome", "evaluate_check", "run_rules"]
