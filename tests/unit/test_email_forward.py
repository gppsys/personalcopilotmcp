"""Tests for EmailForwardService."""

from executive_copilot.services.email_forward import ForwardRule


class TestForwardRule:
    """Tests for ForwardRule matching."""

    def test_match_exact_email(self):
        """Test matching exact sender email."""
        rule = ForwardRule(
            name="test_rule",
            description="Test",
            enabled=True,
            conditions={"from_email": "invoice@example.com"},
            action={"forward_to": "accounting@company.com"},
        )

        class MockEmail:
            sender_email = "invoice@example.com"
            subject = "Your invoice"

        assert rule.matches(MockEmail()) is True

    def test_no_match_different_email(self):
        """Test non-matching sender email."""
        rule = ForwardRule(
            name="test_rule",
            description="Test",
            enabled=True,
            conditions={"from_email": "invoice@example.com"},
            action={"forward_to": "accounting@company.com"},
        )

        class MockEmail:
            sender_email = "other@example.com"
            subject = "Your invoice"

        assert rule.matches(MockEmail()) is False

    def test_match_email_case_insensitive(self):
        """Test case insensitive email matching."""
        rule = ForwardRule(
            name="test_rule",
            description="Test",
            enabled=True,
            conditions={"from_email": "Invoice@Example.COM"},
            action={"forward_to": "accounting@company.com"},
        )

        class MockEmail:
            sender_email = "invoice@example.com"
            subject = "Test"

        assert rule.matches(MockEmail()) is True

    def test_match_domain(self):
        """Test matching sender domain."""
        rule = ForwardRule(
            name="test_rule",
            description="Test",
            enabled=True,
            conditions={"from_domain": "example.com"},
            action={"forward_to": "accounting@company.com"},
        )

        class MockEmail:
            sender_email = "anyone@example.com"
            subject = "Test"

        assert rule.matches(MockEmail()) is True

    def test_match_subject_contains(self):
        """Test matching subject contains."""
        rule = ForwardRule(
            name="test_rule",
            description="Test",
            enabled=True,
            conditions={"subject_contains": "invoice"},
            action={"forward_to": "accounting@company.com"},
        )

        class MockEmail:
            sender_email = "anyone@example.com"
            subject = "Your INVOICE for July"

        assert rule.matches(MockEmail()) is True

    def test_no_match_subject_not_contains(self):
        """Test non-matching subject."""
        rule = ForwardRule(
            name="test_rule",
            description="Test",
            enabled=True,
            conditions={"subject_contains": "invoice"},
            action={"forward_to": "accounting@company.com"},
        )

        class MockEmail:
            sender_email = "anyone@example.com"
            subject = "Weekly newsletter"

        assert rule.matches(MockEmail()) is False

    def test_disabled_rule_no_match(self):
        """Test that disabled rules don't match."""
        rule = ForwardRule(
            name="test_rule",
            description="Test",
            enabled=False,
            conditions={"from_email": "invoice@example.com"},
            action={"forward_to": "accounting@company.com"},
        )

        class MockEmail:
            sender_email = "invoice@example.com"
            subject = "Test"

        assert rule.matches(MockEmail()) is False

    def test_match_multiple_conditions(self):
        """Test matching with multiple conditions (AND logic)."""
        rule = ForwardRule(
            name="test_rule",
            description="Test",
            enabled=True,
            conditions={
                "from_domain": "billing.example.com",
                "subject_contains": "invoice",
            },
            action={"forward_to": "accounting@company.com"},
        )

        class MockEmail:
            sender_email = "no-reply@billing.example.com"
            subject = "Your monthly invoice"

        assert rule.matches(MockEmail()) is True

        class MockEmailWrongDomain:
            sender_email = "no-reply@other.com"
            subject = "Your monthly invoice"

        assert rule.matches(MockEmailWrongDomain()) is False

        class MockEmailWrongSubject:
            sender_email = "no-reply@billing.example.com"
            subject = "Account statement"

        assert rule.matches(MockEmailWrongSubject()) is False


class TestForwardRuleFromDict:
    """Tests for ForwardRule.from_dict."""

    def test_from_dict_full(self):
        """Test creating rule from full dict."""
        data = {
            "name": "anthropic_invoices",
            "description": "Forward Anthropic invoices",
            "enabled": True,
            "conditions": {"from_email": "invoice@anthropic.com"},
            "action": {
                "forward_to": "accounting@company.com",
                "add_message": "Here's the invoice",
                "mark_as_read": True,
            },
        }

        rule = ForwardRule.from_dict(data)

        assert rule.name == "anthropic_invoices"
        assert rule.description == "Forward Anthropic invoices"
        assert rule.enabled is True
        assert rule.conditions["from_email"] == "invoice@anthropic.com"
        assert rule.action["forward_to"] == "accounting@company.com"
        assert rule.action["mark_as_read"] is True

    def test_from_dict_minimal(self):
        """Test creating rule with minimal data."""
        data = {"name": "test_rule"}

        rule = ForwardRule.from_dict(data)

        assert rule.name == "test_rule"
        assert rule.description == ""
        assert rule.enabled is True
        assert rule.conditions == {}
        assert rule.action == {}
