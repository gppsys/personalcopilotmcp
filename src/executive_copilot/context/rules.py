"""Context rule definitions and matching logic."""

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from executive_copilot.context.models import (
    ContextRequest,
    ContextSource,
    SourceRecommendation,
)


class ConditionType(StrEnum):
    """Types of conditions that can be checked."""

    ORGANIZATION_NAME = "organization_name"
    ORGANIZATION_TYPE = "organization_type"
    SENDER_DOMAIN = "sender_domain"
    KEYWORD_PRESENT = "keyword_present"
    TOPIC_MATCH = "topic_match"
    INPUT_TYPE = "input_type"
    CONTENT_CONTAINS = "content_contains"
    CONTENT_REGEX = "content_regex"
    ALWAYS = "always"


class ConditionOperator(StrEnum):
    """Operators for conditions."""

    EQUALS = "equals"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    MATCHES_REGEX = "matches_regex"
    IN_LIST = "in_list"
    NOT_EQUALS = "not_equals"
    NOT_IN_LIST = "not_in_list"


class RuleCondition(BaseModel):
    """A single condition for a context rule."""

    condition_type: ConditionType = Field(description="Type of condition")
    operator: ConditionOperator = Field(
        default=ConditionOperator.EQUALS,
        description="Comparison operator",
    )
    value: Any = Field(description="Value to compare against")
    case_insensitive: bool = Field(default=True, description="Case-insensitive comparison")


class ContextRule(BaseModel):
    """A rule for determining context sources.

    Rules are evaluated in priority order. When a rule matches,
    its sources are added to the recommendations.
    """

    name: str = Field(description="Rule name")
    description: str | None = Field(default=None, description="Rule description")
    priority: int = Field(default=0, description="Rule priority (higher = first)")
    enabled: bool = Field(default=True, description="Whether rule is active")

    # Conditions (all must match for rule to apply)
    conditions: list[RuleCondition] = Field(
        default_factory=list,
        description="Conditions that must all be true",
    )

    # Sources to add when rule matches
    sources: list[ContextSource] = Field(description="Sources to consult")
    source_priorities: dict[str, int] = Field(
        default_factory=dict,
        description="Priority override per source",
    )
    required_sources: list[ContextSource] = Field(
        default_factory=list,
        description="Sources that are required (not optional)",
    )

    # Stop processing
    terminal: bool = Field(
        default=False,
        description="If true, stop processing further rules after this one",
    )


class RuleMatcher:
    """Matches context rules against requests."""

    def __init__(self, rules: list[ContextRule]) -> None:
        """Initialize matcher with rules.

        Args:
            rules: Rules to use for matching, will be sorted by priority
        """
        self._rules = sorted(
            [r for r in rules if r.enabled],
            key=lambda r: -r.priority,
        )

    def match(self, request: ContextRequest) -> list[tuple[ContextRule, list[SourceRecommendation]]]:
        """Match rules against a request.

        Args:
            request: Context request to match

        Returns:
            List of (matched_rule, recommendations) tuples
        """
        matches: list[tuple[ContextRule, list[SourceRecommendation]]] = []

        for rule in self._rules:
            if self._rule_matches(rule, request):
                recommendations = self._create_recommendations(rule)
                matches.append((rule, recommendations))

                if rule.terminal:
                    break

        return matches

    def _rule_matches(self, rule: ContextRule, request: ContextRequest) -> bool:
        """Check if a rule matches the request.

        Args:
            rule: Rule to check
            request: Request to match against

        Returns:
            True if all conditions match
        """
        if not rule.conditions:
            return False

        for condition in rule.conditions:
            if not self._condition_matches(condition, request):
                return False

        return True

    def _condition_matches(
        self,
        condition: RuleCondition,
        request: ContextRequest,
    ) -> bool:
        """Check if a single condition matches.

        Args:
            condition: Condition to check
            request: Request to match against

        Returns:
            True if condition matches
        """
        value = self._get_condition_value(condition.condition_type, request)

        if value is None and condition.condition_type != ConditionType.ALWAYS:
            return False

        return self._compare(value, condition)

    def _get_condition_value(
        self,
        condition_type: ConditionType,
        request: ContextRequest,
    ) -> Any:
        """Extract the value to check from the request.

        Args:
            condition_type: Type of condition
            request: Request to extract from

        Returns:
            Value to compare
        """
        match condition_type:
            case ConditionType.ORGANIZATION_NAME:
                return request.organization_name
            case ConditionType.SENDER_DOMAIN:
                return request.sender_domain
            case ConditionType.INPUT_TYPE:
                return request.input_type.value
            case ConditionType.CONTENT_CONTAINS | ConditionType.CONTENT_REGEX:
                return request.content
            case ConditionType.KEYWORD_PRESENT:
                return request.keywords
            case ConditionType.TOPIC_MATCH:
                return request.topics
            case ConditionType.ALWAYS:
                return True
            case _:
                return None

    def _compare(self, value: Any, condition: RuleCondition) -> bool:
        """Compare a value against a condition.

        Args:
            value: Value to compare
            condition: Condition with operator and expected value

        Returns:
            True if comparison passes
        """
        if condition.condition_type == ConditionType.ALWAYS:
            return True

        expected = condition.value
        ci = condition.case_insensitive

        # Handle string comparisons
        if isinstance(value, str) and isinstance(expected, str):
            if ci:
                value = value.lower()
                expected = expected.lower()

            match condition.operator:
                case ConditionOperator.EQUALS:
                    return bool(value == expected)
                case ConditionOperator.NOT_EQUALS:
                    return bool(value != expected)
                case ConditionOperator.CONTAINS:
                    return bool(expected in value)
                case ConditionOperator.STARTS_WITH:
                    return bool(value.startswith(expected))
                case ConditionOperator.ENDS_WITH:
                    return bool(value.endswith(expected))
                case ConditionOperator.MATCHES_REGEX:
                    flags = re.IGNORECASE if ci else 0
                    return bool(re.search(expected, value, flags))

        # Handle list comparisons
        if isinstance(value, list):
            if ci and all(isinstance(v, str) for v in value):
                value = [v.lower() for v in value]
            if ci and isinstance(expected, str):
                expected = expected.lower()

            match condition.operator:
                case ConditionOperator.CONTAINS | ConditionOperator.IN_LIST:
                    if isinstance(expected, list):
                        return any(e in value for e in expected)
                    return expected in value
                case ConditionOperator.NOT_IN_LIST:
                    if isinstance(expected, list):
                        return not any(e in value for e in expected)
                    return expected not in value

        # Handle IN_LIST for strings
        if isinstance(expected, list):
            check_value = value.lower() if ci and isinstance(value, str) else value
            check_list = (
                [e.lower() for e in expected]
                if ci and all(isinstance(e, str) for e in expected)
                else expected
            )

            match condition.operator:
                case ConditionOperator.IN_LIST:
                    return check_value in check_list
                case ConditionOperator.NOT_IN_LIST:
                    return check_value not in check_list

        return False

    def _create_recommendations(
        self,
        rule: ContextRule,
    ) -> list[SourceRecommendation]:
        """Create source recommendations from a matched rule.

        Args:
            rule: Matched rule

        Returns:
            List of recommendations
        """
        recommendations: list[SourceRecommendation] = []

        for source in rule.sources:
            priority = rule.source_priorities.get(source.value, rule.priority)
            required = source in rule.required_sources

            recommendations.append(
                SourceRecommendation(
                    source=source,
                    priority=priority,
                    reason=f"Matched rule: {rule.name}",
                    required=required,
                )
            )

        return recommendations
