"""Tests for the Context Engine."""


from executive_copilot.config import AppConfig, ContextRuleConfig
from executive_copilot.context import (
    ContextEngine,
    ContextRequest,
    ContextSource,
)
from executive_copilot.context.models import ContextInputType
from executive_copilot.context.rules import (
    ConditionOperator,
    ConditionType,
    ContextRule,
    RuleCondition,
    RuleMatcher,
)


class TestRuleMatcher:
    """Tests for the RuleMatcher."""

    def test_matches_organization_name(self) -> None:
        rule = ContextRule(
            name="paycode_rule",
            conditions=[
                RuleCondition(
                    condition_type=ConditionType.ORGANIZATION_NAME,
                    operator=ConditionOperator.EQUALS,
                    value="Paycode",
                )
            ],
            sources=[ContextSource.ASANA, ContextSource.DOCUMENTATION],
        )

        matcher = RuleMatcher([rule])

        request = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content="Test content",
            organization_name="Paycode",
        )

        matches = matcher.match(request)
        assert len(matches) == 1
        assert matches[0][0].name == "paycode_rule"

    def test_matches_sender_domain(self) -> None:
        rule = ContextRule(
            name="domain_rule",
            conditions=[
                RuleCondition(
                    condition_type=ConditionType.SENDER_DOMAIN,
                    operator=ConditionOperator.EQUALS,
                    value="example.com",
                )
            ],
            sources=[ContextSource.MEMORY],
        )

        matcher = RuleMatcher([rule])

        request = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content="Test",
            sender_domain="example.com",
        )

        matches = matcher.match(request)
        assert len(matches) == 1

    def test_matches_content_contains(self) -> None:
        rule = ContextRule(
            name="technical_rule",
            conditions=[
                RuleCondition(
                    condition_type=ConditionType.CONTENT_CONTAINS,
                    operator=ConditionOperator.CONTAINS,
                    value="technical",
                )
            ],
            sources=[ContextSource.DOCUMENTATION],
        )

        matcher = RuleMatcher([rule])

        request = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content="This is a technical question about the API.",
        )

        matches = matcher.match(request)
        assert len(matches) == 1

    def test_case_insensitive_matching(self) -> None:
        rule = ContextRule(
            name="case_rule",
            conditions=[
                RuleCondition(
                    condition_type=ConditionType.ORGANIZATION_NAME,
                    operator=ConditionOperator.EQUALS,
                    value="PAYCODE",
                    case_insensitive=True,
                )
            ],
            sources=[ContextSource.MEMORY],
        )

        matcher = RuleMatcher([rule])

        request = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content="Test",
            organization_name="paycode",
        )

        matches = matcher.match(request)
        assert len(matches) == 1

    def test_in_list_operator(self) -> None:
        rule = ContextRule(
            name="domain_list_rule",
            conditions=[
                RuleCondition(
                    condition_type=ConditionType.SENDER_DOMAIN,
                    operator=ConditionOperator.IN_LIST,
                    value=["example.com", "test.com", "other.com"],
                )
            ],
            sources=[ContextSource.MEMORY],
        )

        matcher = RuleMatcher([rule])

        request = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content="Test",
            sender_domain="test.com",
        )

        matches = matcher.match(request)
        assert len(matches) == 1

    def test_multiple_conditions_all_must_match(self) -> None:
        rule = ContextRule(
            name="multi_condition_rule",
            conditions=[
                RuleCondition(
                    condition_type=ConditionType.ORGANIZATION_NAME,
                    operator=ConditionOperator.EQUALS,
                    value="Paycode",
                ),
                RuleCondition(
                    condition_type=ConditionType.INPUT_TYPE,
                    operator=ConditionOperator.EQUALS,
                    value="email",
                ),
            ],
            sources=[ContextSource.ASANA],
        )

        matcher = RuleMatcher([rule])

        # Both conditions match
        request1 = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content="Test",
            organization_name="Paycode",
        )
        assert len(matcher.match(request1)) == 1

        # Only one condition matches
        request2 = ContextRequest(
            input_type=ContextInputType.QUESTION,
            content="Test",
            organization_name="Paycode",
        )
        assert len(matcher.match(request2)) == 0

    def test_priority_ordering(self) -> None:
        rules = [
            ContextRule(
                name="low_priority",
                priority=10,
                conditions=[
                    RuleCondition(
                        condition_type=ConditionType.ALWAYS,
                        value=True,
                    )
                ],
                sources=[ContextSource.MEMORY],
            ),
            ContextRule(
                name="high_priority",
                priority=100,
                conditions=[
                    RuleCondition(
                        condition_type=ConditionType.ALWAYS,
                        value=True,
                    )
                ],
                sources=[ContextSource.ASANA],
            ),
        ]

        matcher = RuleMatcher(rules)

        request = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content="Test",
        )

        matches = matcher.match(request)
        assert len(matches) == 2
        assert matches[0][0].name == "high_priority"
        assert matches[1][0].name == "low_priority"

    def test_terminal_rule_stops_processing(self) -> None:
        rules = [
            ContextRule(
                name="terminal_rule",
                priority=100,
                terminal=True,
                conditions=[
                    RuleCondition(
                        condition_type=ConditionType.ALWAYS,
                        value=True,
                    )
                ],
                sources=[ContextSource.MEMORY],
            ),
            ContextRule(
                name="should_not_match",
                priority=50,
                conditions=[
                    RuleCondition(
                        condition_type=ConditionType.ALWAYS,
                        value=True,
                    )
                ],
                sources=[ContextSource.ASANA],
            ),
        ]

        matcher = RuleMatcher(rules)

        request = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content="Test",
        )

        matches = matcher.match(request)
        assert len(matches) == 1
        assert matches[0][0].name == "terminal_rule"


class TestContextEngine:
    """Tests for the ContextEngine."""

    def test_analyze_email_with_organization(self) -> None:
        config = AppConfig(
            organizations=[
                {
                    "name": "Paycode",
                    "organization_type": "employer",
                    "domains": ["paycode.com.mx"],
                    "context_sources": ["asana", "documentation"],
                }
            ],
            context_rules=[
                ContextRuleConfig(
                    name="paycode_technical",
                    priority=100,
                    conditions={"domain": "paycode.com.mx"},
                    sources=["asana", "documentation", "memory"],
                )
            ],
        )

        engine = ContextEngine(config)

        result = engine.analyze_email(
            content="Technical question about the API",
            sender_email="john@paycode.com.mx",
            subject="API Question",
        )

        assert result.detected_organization_name == "Paycode"
        assert len(result.matched_rules) > 0
        assert ContextSource.ASANA in result.ordered_sources

    def test_analyze_question(self) -> None:
        config = AppConfig()
        engine = ContextEngine(config)

        result = engine.analyze_question(
            question="What is the status of the project?",
            organization_name="TestOrg",
        )

        assert result.request.input_type == ContextInputType.QUESTION
        assert len(result.sources) > 0

    def test_default_rules_applied(self) -> None:
        config = AppConfig()
        engine = ContextEngine(config)

        result = engine.analyze_email(
            content="Hello",
            sender_email="user@example.com",
        )

        # Should have default sources
        assert any(s.source == ContextSource.MEMORY for s in result.sources)
