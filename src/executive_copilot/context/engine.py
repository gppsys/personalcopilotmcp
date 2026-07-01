"""Context Engine implementation."""

from executive_copilot.config import AppConfig, ContextRuleConfig
from executive_copilot.context.models import (
    ContextInputType,
    ContextRequest,
    ContextResult,
    ContextSource,
    SourceRecommendation,
)
from executive_copilot.context.rules import (
    ConditionOperator,
    ConditionType,
    ContextRule,
    RuleCondition,
    RuleMatcher,
)
from executive_copilot.logging import get_logger

logger = get_logger(__name__)


class ContextEngine:
    """Engine for determining which information sources to consult.

    The Context Engine analyzes inputs (emails, questions, drafts)
    and determines which information sources should be consulted
    before responding. Configuration-driven rules allow customization
    without code changes.
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize context engine with configuration.

        Args:
            config: Application configuration containing context rules
        """
        self._config = config
        self._rules = self._build_rules(config.context_rules)
        self._matcher = RuleMatcher(self._rules)
        logger.info("Context engine initialized", rule_count=len(self._rules))

    def analyze(self, request: ContextRequest) -> ContextResult:
        """Analyze a request and determine context sources.

        Args:
            request: Context request to analyze

        Returns:
            Context result with recommended sources
        """
        logger.debug(
            "Analyzing context request",
            input_type=request.input_type.value,
            organization=request.organization_name,
        )

        # Match rules
        matches = self._matcher.match(request)

        # Collect all recommendations
        all_recommendations: list[SourceRecommendation] = []
        matched_rules: list[str] = []

        for rule, recommendations in matches:
            matched_rules.append(rule.name)
            all_recommendations.extend(recommendations)

        # Deduplicate and merge recommendations
        merged = self._merge_recommendations(all_recommendations)

        # Add default sources if none matched
        if not merged:
            merged = self._get_default_sources(request)

        # Detect organization from config if not provided
        detected_org_id = request.organization_id
        detected_org_name = request.organization_name

        if request.sender_domain and not detected_org_name:
            org_config = self._config.get_organization_by_domain(request.sender_domain)
            if org_config:
                detected_org_name = org_config.name

        # Calculate confidence
        confidence = self._calculate_confidence(matches, request)

        result = ContextResult(
            request=request,
            detected_organization_id=detected_org_id,
            detected_organization_name=detected_org_name,
            detected_topics=request.topics,
            sources=merged,
            matched_rules=matched_rules,
            confidence=confidence,
        )

        logger.debug(
            "Context analysis complete",
            matched_rules=len(matched_rules),
            sources=len(merged),
            confidence=confidence,
        )

        return result

    def analyze_email(
        self,
        content: str,
        sender_email: str,
        subject: str | None = None,
    ) -> ContextResult:
        """Convenience method to analyze an email.

        Args:
            content: Email body content
            sender_email: Sender email address
            subject: Email subject

        Returns:
            Context result
        """
        sender_domain = sender_email.rsplit("@", maxsplit=1)[-1] if "@" in sender_email else None
        full_content = f"{subject or ''}\n\n{content}"

        request = ContextRequest(
            input_type=ContextInputType.EMAIL,
            content=full_content,
            sender_email=sender_email,
            sender_domain=sender_domain,
        )

        return self.analyze(request)

    def analyze_question(
        self,
        question: str,
        organization_name: str | None = None,
    ) -> ContextResult:
        """Convenience method to analyze a question.

        Args:
            question: Question text
            organization_name: Optional organization context

        Returns:
            Context result
        """
        request = ContextRequest(
            input_type=ContextInputType.QUESTION,
            content=question,
            organization_name=organization_name,
        )

        return self.analyze(request)

    def _build_rules(
        self,
        rule_configs: list[ContextRuleConfig],
    ) -> list[ContextRule]:
        """Build context rules from configuration.

        Args:
            rule_configs: Rule configurations

        Returns:
            List of context rules
        """
        rules: list[ContextRule] = []

        for config in rule_configs:
            if not config.enabled:
                continue

            conditions = self._parse_conditions(config.conditions)
            sources = [ContextSource(s) for s in config.sources if s in ContextSource.__members__.values()]

            rule = ContextRule(
                name=config.name,
                description=config.description,
                priority=config.priority,
                enabled=True,
                conditions=conditions,
                sources=sources,
            )
            rules.append(rule)

        # Add built-in default rules
        rules.extend(self._get_builtin_rules())

        return rules

    def _parse_conditions(
        self,
        conditions_config: dict[str, object],
    ) -> list[RuleCondition]:
        """Parse conditions from configuration dictionary.

        Args:
            conditions_config: Conditions configuration

        Returns:
            List of rule conditions
        """
        conditions: list[RuleCondition] = []

        for key, value in conditions_config.items():
            condition_type = self._map_condition_type(key)
            if condition_type is None:
                logger.warning("Unknown condition type", condition_type=key)
                continue

            operator = ConditionOperator.EQUALS
            if isinstance(value, list):
                operator = ConditionOperator.IN_LIST

            conditions.append(
                RuleCondition(
                    condition_type=condition_type,
                    operator=operator,
                    value=value,
                )
            )

        return conditions

    def _map_condition_type(self, key: str) -> ConditionType | None:
        """Map configuration key to condition type."""
        mapping = {
            "organization": ConditionType.ORGANIZATION_NAME,
            "organization_name": ConditionType.ORGANIZATION_NAME,
            "organization_type": ConditionType.ORGANIZATION_TYPE,
            "domain": ConditionType.SENDER_DOMAIN,
            "sender_domain": ConditionType.SENDER_DOMAIN,
            "keywords": ConditionType.KEYWORD_PRESENT,
            "topics": ConditionType.TOPIC_MATCH,
            "input_type": ConditionType.INPUT_TYPE,
            "content_contains": ConditionType.CONTENT_CONTAINS,
            "content_regex": ConditionType.CONTENT_REGEX,
        }
        return mapping.get(key)

    def _get_builtin_rules(self) -> list[ContextRule]:
        """Get built-in default context rules."""
        return [
            # Default rule for emails - always check memory and previous threads
            ContextRule(
                name="default_email",
                description="Default sources for email context",
                priority=-100,
                conditions=[
                    RuleCondition(
                        condition_type=ConditionType.INPUT_TYPE,
                        operator=ConditionOperator.EQUALS,
                        value="email",
                    )
                ],
                sources=[
                    ContextSource.MEMORY,
                    ContextSource.PREVIOUS_THREADS,
                    ContextSource.PEOPLE,
                    ContextSource.ORGANIZATIONS,
                ],
            ),
            # Default rule for questions
            ContextRule(
                name="default_question",
                description="Default sources for questions",
                priority=-100,
                conditions=[
                    RuleCondition(
                        condition_type=ConditionType.INPUT_TYPE,
                        operator=ConditionOperator.EQUALS,
                        value="question",
                    )
                ],
                sources=[
                    ContextSource.MEMORY,
                    ContextSource.DOCUMENTATION,
                ],
            ),
            # Default rule for meetings
            ContextRule(
                name="default_meeting",
                description="Default sources for meeting context",
                priority=-100,
                conditions=[
                    RuleCondition(
                        condition_type=ConditionType.INPUT_TYPE,
                        operator=ConditionOperator.EQUALS,
                        value="meeting",
                    )
                ],
                sources=[
                    ContextSource.CALENDAR,
                    ContextSource.PEOPLE,
                    ContextSource.PREVIOUS_THREADS,
                    ContextSource.TASKS,
                ],
            ),
        ]

    def _merge_recommendations(
        self,
        recommendations: list[SourceRecommendation],
    ) -> list[SourceRecommendation]:
        """Merge and deduplicate recommendations.

        Args:
            recommendations: All recommendations from matched rules

        Returns:
            Merged recommendations with highest priority for each source
        """
        by_source: dict[ContextSource, SourceRecommendation] = {}

        for rec in recommendations:
            existing = by_source.get(rec.source)
            if existing is None:
                by_source[rec.source] = rec
            elif rec.priority > existing.priority:
                # Keep higher priority
                by_source[rec.source] = SourceRecommendation(
                    source=rec.source,
                    priority=rec.priority,
                    reason=f"{existing.reason}; {rec.reason}",
                    required=existing.required or rec.required,
                )
            elif rec.required and not existing.required:
                # Upgrade to required
                by_source[rec.source] = existing.model_copy(update={"required": True})

        # Sort by priority
        return sorted(by_source.values(), key=lambda r: -r.priority)

    def _get_default_sources(
        self,
        request: ContextRequest,
    ) -> list[SourceRecommendation]:
        """Get default sources when no rules match.

        Args:
            request: Original request

        Returns:
            Default recommendations
        """
        return [
            SourceRecommendation(
                source=ContextSource.MEMORY,
                priority=0,
                reason="Default source",
                required=False,
            ),
        ]

    def _calculate_confidence(
        self,
        matches: list[tuple[ContextRule, list[SourceRecommendation]]],
        request: ContextRequest,
    ) -> float:
        """Calculate confidence in recommendations.

        Args:
            matches: Matched rules and their recommendations
            request: Original request

        Returns:
            Confidence score between 0 and 1
        """
        if not matches:
            return 0.1

        # Higher confidence with more specific matches
        confidence = 0.5

        # Boost for organization detection
        if request.organization_name or request.organization_id:
            confidence += 0.2

        # Boost for multiple matching rules
        if len(matches) > 1:
            confidence += 0.1

        # Boost for high-priority rules
        if any(rule.priority > 50 for rule, _ in matches):
            confidence += 0.1

        return min(confidence, 1.0)
