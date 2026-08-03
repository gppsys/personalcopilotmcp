"""Service for automatic email forwarding based on rules."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.config import AppConfig
from executive_copilot.logging import get_logger
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider

logger = get_logger(__name__)


@dataclass
class ForwardRule:
    """A rule for automatic email forwarding."""

    name: str
    description: str
    enabled: bool
    conditions: dict[str, Any]
    action: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ForwardRule":
        """Create rule from dictionary."""
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            conditions=data.get("conditions", {}),
            action=data.get("action", {}),
        )

    def matches(self, email: Any) -> bool:
        """Check if email matches this rule's conditions."""
        if not self.enabled:
            return False

        from_email = self.conditions.get("from_email")
        if from_email:
            sender = getattr(email, "sender_email", "") or ""
            if sender.lower() != from_email.lower():
                return False

        from_domain = self.conditions.get("from_domain")
        if from_domain:
            sender = getattr(email, "sender_email", "") or ""
            if not sender.lower().endswith(f"@{from_domain.lower()}"):
                return False

        subject_contains = self.conditions.get("subject_contains")
        if subject_contains:
            subject = getattr(email, "subject", "") or ""
            if subject_contains.lower() not in subject.lower():
                return False

        subject_regex = self.conditions.get("subject_regex")
        if subject_regex:
            import re

            subject = getattr(email, "subject", "") or ""
            if not re.search(subject_regex, subject, re.IGNORECASE):
                return False

        return True


class EmailForwardService:
    """Service for automatic email forwarding."""

    def __init__(
        self,
        config: AppConfig,
        credential_manager: CredentialManager,
        config_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.credential_manager = credential_manager
        self.config_dir = config_dir or Path("config")
        self._rules: list[ForwardRule] = []
        self._processed_ids: set[str] = set()
        self._load_rules()

    def _load_rules(self) -> None:
        """Load forward rules from config file."""
        rules_file = self.config_dir / "email_forward_rules.yaml"
        if not rules_file.exists():
            logger.warning("No email forward rules file found", path=str(rules_file))
            return

        with open(rules_file) as f:
            data = yaml.safe_load(f) or {}

        rules_data = data.get("email_forward_rules", [])
        self._rules = [ForwardRule.from_dict(r) for r in rules_data]
        logger.info("Loaded email forward rules", count=len(self._rules))

    def get_rules(self) -> list[dict[str, Any]]:
        """Get all rules as dictionaries."""
        return [
            {
                "name": r.name,
                "description": r.description,
                "enabled": r.enabled,
                "conditions": r.conditions,
                "action": r.action,
            }
            for r in self._rules
        ]

    def _get_email_accounts(self) -> list[dict[str, str]]:
        """Get all configured email accounts."""
        accounts = []
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "email":
                    accounts.append({
                        "account_id": pa.account_id,
                        "provider": pa.provider_name,
                    })
        return accounts

    def _is_authorized(self, provider_name: str, account_id: str) -> bool:
        """Check if account has valid credentials."""
        cred = self.credential_manager.get_credential("email", provider_name, account_id)
        return cred is not None and cred.get("access_token") is not None

    def _get_email_provider(
        self, provider_name: str, account_id: str
    ) -> GmailProvider | OutlookProvider | None:
        """Get email provider instance."""
        if provider_name == "gmail":
            return GmailProvider(self.credential_manager, account_id)
        elif provider_name == "outlook":
            return OutlookProvider(self.credential_manager, account_id)
        return None

    async def process_new_emails(self, hours_back: int = 1) -> dict[str, Any]:
        """Process new emails and apply forward rules.

        Args:
            hours_back: How many hours back to check for emails

        Returns:
            Statistics about processed emails
        """
        emails_checked = 0
        emails_matched = 0
        emails_forwarded = 0
        errors: list[str] = []
        forwarded_details: list[dict[str, Any]] = []

        def make_stats() -> dict[str, Any]:
            return {
                "emails_checked": emails_checked,
                "emails_matched": emails_matched,
                "emails_forwarded": emails_forwarded,
                "errors": errors,
                "forwarded_details": forwarded_details,
            }

        if not self._rules:
            logger.info("No forward rules configured")
            return make_stats()

        enabled_rules = [r for r in self._rules if r.enabled]
        if not enabled_rules:
            logger.info("No enabled forward rules")
            return make_stats()

        accounts = self._get_email_accounts()
        logger.info("Processing emails for forward rules", accounts=len(accounts), rules=len(enabled_rules))

        for account in accounts:
            provider_name = account["provider"]
            account_id = account["account_id"]

            if not self._is_authorized(provider_name, account_id):
                logger.debug("Skipping unauthorized account", account=account_id)
                continue

            provider = self._get_email_provider(provider_name, account_id)
            if not provider:
                continue

            try:
                fetched = await provider.get_unread(max_results=50)
                emails_checked += len(fetched)

                for email in fetched:
                    email_id = str(getattr(email, "id", None) or getattr(email, "message_id", ""))
                    if email_id in self._processed_ids:
                        continue

                    for rule in enabled_rules:
                        if rule.matches(email):
                            emails_matched += 1
                            logger.info(
                                "Email matched rule",
                                rule=rule.name,
                                subject=getattr(email, "subject", ""),
                                sender=getattr(email, "sender_email", ""),
                            )

                            result = await self._forward_email(
                                provider, email, rule, account_id
                            )

                            if result["success"]:
                                emails_forwarded += 1
                                forwarded_details.append({
                                    "rule": rule.name,
                                    "subject": getattr(email, "subject", ""),
                                    "sender": getattr(email, "sender_email", ""),
                                    "forwarded_to": rule.action.get("forward_to"),
                                    "timestamp": datetime.now(UTC).isoformat(),
                                })
                                self._processed_ids.add(email_id)
                            else:
                                errors.append(result.get("error", "Unknown error"))

                            break

            except Exception as e:
                logger.error("Error processing emails", account=account_id, error=str(e))
                errors.append(f"Account {account_id}: {e!s}")

        return make_stats()

    async def _forward_email(
        self,
        provider: GmailProvider | OutlookProvider,
        email: Any,
        rule: ForwardRule,
        account_id: str,
    ) -> dict[str, Any]:
        """Forward an email according to rule action."""
        forward_to = rule.action.get("forward_to")
        if not forward_to:
            return {"success": False, "error": "No forward_to address in rule"}

        add_message = rule.action.get("add_message", "")
        cc = rule.action.get("cc")
        mark_as_read = rule.action.get("mark_as_read", False)
        add_label = rule.action.get("add_label")

        try:
            original_subject = getattr(email, "subject", "No subject")
            original_body = getattr(email, "body", "") or getattr(email, "snippet", "")
            original_sender = getattr(email, "sender_email", "Unknown")
            original_date = getattr(email, "received_at", datetime.now(UTC))

            forward_subject = f"Fwd: {original_subject}"

            forward_body = f"""{add_message}

---------- Forwarded message ----------
From: {original_sender}
Date: {original_date}
Subject: {original_subject}

{original_body}
"""

            to_list = [forward_to] if isinstance(forward_to, str) else forward_to
            cc_list = [cc] if isinstance(cc, str) else cc

            result = await provider.send_email(
                to=to_list,
                subject=forward_subject,
                body=forward_body,
                cc=cc_list,
            )

            if mark_as_read:
                msg_id = getattr(email, "id", None)
                if msg_id:
                    try:
                        await provider.mark_as_read(str(msg_id))
                    except Exception as e:
                        logger.warning("Failed to mark as read", error=str(e))

            if add_label and hasattr(provider, "add_label"):
                msg_id = getattr(email, "id", None)
                if msg_id:
                    try:
                        await provider.add_label(str(msg_id), add_label)
                    except Exception as e:
                        logger.warning("Failed to add label", error=str(e))

            logger.info(
                "Email forwarded",
                rule=rule.name,
                to=forward_to,
                subject=original_subject,
            )

            sent_id = str(getattr(result, "id", "")) if result else None
            return {"success": True, "message_id": sent_id}

        except Exception as e:
            logger.error("Failed to forward email", rule=rule.name, error=str(e))
            return {"success": False, "error": str(e)}

    async def test_rules(self, hours_back: int = 24) -> dict[str, Any]:
        """Test rules against recent emails without actually forwarding.

        Args:
            hours_back: How many hours back to check

        Returns:
            List of emails that would be forwarded
        """
        matches = []

        enabled_rules = [r for r in self._rules if r.enabled]
        if not enabled_rules:
            return {"matches": [], "message": "No enabled rules"}

        accounts = self._get_email_accounts()

        for account in accounts:
            provider_name = account["provider"]
            account_id = account["account_id"]

            if not self._is_authorized(provider_name, account_id):
                continue

            provider = self._get_email_provider(provider_name, account_id)
            if not provider:
                continue

            try:
                emails = await provider.get_unread(max_results=100)

                for email in emails:
                    for rule in enabled_rules:
                        if rule.matches(email):
                            matches.append({
                                "rule": rule.name,
                                "subject": getattr(email, "subject", ""),
                                "sender": getattr(email, "sender_email", ""),
                                "received_at": str(getattr(email, "received_at", "")),
                                "would_forward_to": rule.action.get("forward_to"),
                            })
                            break

            except Exception as e:
                logger.error("Error testing rules", account=account_id, error=str(e))

        return {"matches": matches, "total": len(matches)}
