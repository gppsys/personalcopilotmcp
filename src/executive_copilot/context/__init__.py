"""Context Engine for determining information sources.

The Context Engine is responsible for analyzing inputs (emails, questions,
drafts) and determining which information sources should be consulted
before responding.
"""

from executive_copilot.context.engine import ContextEngine
from executive_copilot.context.models import ContextRequest, ContextResult, ContextSource
from executive_copilot.context.rules import ContextRule, RuleCondition, RuleMatcher

__all__ = [
    "ContextEngine",
    "ContextRequest",
    "ContextResult",
    "ContextRule",
    "ContextSource",
    "RuleCondition",
    "RuleMatcher",
]
