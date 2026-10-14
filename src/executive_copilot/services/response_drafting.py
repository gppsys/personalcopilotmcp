"""Service for drafting email responses with context and style matching."""

from datetime import datetime
from typing import Any

from executive_copilot.config import AppConfig
from executive_copilot.credentials import CredentialManager
from executive_copilot.database.connection import DatabaseManager
from executive_copilot.database.repositories import (
    CommitmentRepository,
    DelegationRepository,
    FollowUpRepository,
)
from executive_copilot.logging import get_logger
from executive_copilot.models.email import Email
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider

logger = get_logger(__name__)

DRAFT_PROMPT = """You are drafting an email response on behalf of the user.

CONTEXT:
{context}

THREAD HISTORY:
{thread_history}

USER'S WRITING STYLE (based on recent emails):
{style_examples}

OPEN ITEMS WITH THIS PERSON:
{open_items}

INSTRUCTIONS:
{instructions}

Write a professional email response that:
1. Addresses the key points from the thread
2. Matches the user's typical writing style and tone
3. Is concise but complete
4. Acknowledges any commitments or action items appropriately

Return ONLY the email body text. Do not include subject line, greeting preamble like
"Here's a draft", or signature - just the email content starting with the greeting.
"""

FOLLOW_UP_PROMPT = """You are drafting a follow-up email for a message that hasn't
received a response.

ORIGINAL EMAIL (sent by user):
Subject: {subject}
To: {recipients}
Date: {date}
Body:
{body}

DAYS SINCE SENT: {days_waiting}

USER'S WRITING STYLE:
{style_examples}

OPEN ITEMS WITH RECIPIENT:
{open_items}

INSTRUCTIONS:
{instructions}

Write a polite follow-up email that:
1. References the original email naturally
2. Restates the key ask or question briefly
3. Maintains a professional but friendly tone
4. Matches the user's typical writing style
5. Doesn't sound aggressive or impatient

Return ONLY the email body text. Do not include subject line or signature -
just the email content starting with the greeting.
"""


class ResponseDraftingService:
    """Service for generating contextual email drafts."""

    def __init__(
        self,
        config: AppConfig,
        credential_manager: CredentialManager,
        db_manager: DatabaseManager,
        ai_service: Any = None,
    ) -> None:
        self.config = config
        self.credential_manager = credential_manager
        self.db_manager = db_manager
        self._ai_service = ai_service

    def _get_email_accounts(self) -> list[dict[str, str]]:
        """Get all configured email accounts."""
        accounts = []
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "email":
                    accounts.append({
                        "account_id": pa.account_id,
                        "provider": pa.provider_name,
                        "organization": org.name,
                    })
        return accounts

    def _is_authorized(self, provider_name: str, account_id: str) -> bool:
        """Check if account has valid credentials."""
        cred = self.credential_manager.get_credential("email", provider_name, account_id)
        return cred is not None and cred.get("access_token") is not None

    def _get_provider(self, provider_name: str, account_id: str) -> GmailProvider | OutlookProvider:
        """Get email provider instance."""
        if provider_name == "gmail":
            return GmailProvider(self.credential_manager, account_id)
        elif provider_name == "outlook":
            return OutlookProvider(self.credential_manager, account_id)
        raise ValueError(f"Unknown email provider: {provider_name}")

    def _find_account(self, account_id: str) -> dict[str, str] | None:
        """Find account by ID."""
        for acc in self._get_email_accounts():
            if acc["account_id"] == account_id:
                return acc
        return None

    async def draft_reply(
        self,
        thread_id: str,
        account_id: str,
        instructions: str | None = None,
        include_style: bool = True,
        create_draft: bool = False,
    ) -> dict[str, Any]:
        """Draft a reply to an email thread.

        Args:
            thread_id: Email thread ID
            account_id: Email account containing the thread
            instructions: Optional specific instructions for the draft
            include_style: Whether to analyze user's style from sent emails
            create_draft: Whether to create a draft in the email provider

        Returns:
            Draft content and metadata
        """
        acc = self._find_account(account_id)
        if not acc:
            return {"error": f"Account {account_id} not found"}

        if not self._is_authorized(acc["provider"], acc["account_id"]):
            return {"error": f"Account {account_id} not authorized"}

        provider = self._get_provider(acc["provider"], acc["account_id"])

        thread_emails = await provider.get_thread(thread_id)
        if not thread_emails:
            return {"error": f"Thread {thread_id} not found"}

        thread_history = self._format_thread(thread_emails)

        latest_email = thread_emails[-1]
        reply_to = latest_email.sender.address
        subject = latest_email.subject or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        style_examples = ""
        if include_style and self._ai_service:
            style_examples = await self._get_style_examples(provider)

        open_items = await self._get_open_items(reply_to)

        context = f"Replying to email from {latest_email.sender.display_name or reply_to}"
        if latest_email.received_at:
            context += f" received on {latest_email.received_at.strftime('%B %d, %Y')}"

        draft_body = await self._generate_draft(
            prompt_template=DRAFT_PROMPT,
            context=context,
            thread_history=thread_history,
            style_examples=style_examples,
            open_items=open_items,
            instructions=instructions or "Write a helpful and complete response.",
        )

        if not draft_body:
            return {"error": "Failed to generate draft"}

        result: dict[str, Any] = {
            "draft_body": draft_body,
            "subject": subject,
            "to": [reply_to],
            "thread_id": thread_id,
            "account": account_id,
            "in_reply_to": latest_email.sender.address,
        }

        if create_draft:
            try:
                draft_result = await provider.create_draft(
                    to=[reply_to],
                    subject=subject,
                    body=draft_body,
                    thread_id=thread_id,
                )
                result["draft_id"] = draft_result.get("id")
                result["draft_created"] = True
            except Exception as e:
                logger.warning("Failed to create draft in provider", error=str(e))
                result["draft_created"] = False
                result["draft_error"] = str(e)

        return result

    async def draft_follow_up(
        self,
        thread_id: str | None = None,
        account_id: str | None = None,
        follow_up_id: str | None = None,
        instructions: str | None = None,
        include_style: bool = True,
        create_draft: bool = False,
    ) -> dict[str, Any]:
        """Draft a follow-up for an email that hasn't received a response.

        Args:
            thread_id: Thread ID containing the email
            account_id: Email account
            follow_up_id: Follow-up item ID (to get context from database)
            instructions: Optional specific instructions
            include_style: Whether to match user's writing style
            create_draft: Whether to create draft in provider

        Returns:
            Draft content and metadata
        """
        if not account_id:
            accounts = self._get_email_accounts()
            for found_acc in accounts:
                if self._is_authorized(found_acc["provider"], found_acc["account_id"]):
                    account_id = found_acc["account_id"]
                    break

        if not account_id:
            return {"error": "No authorized email account available"}

        acc = self._find_account(account_id)
        if not acc:
            return {"error": f"Account {account_id} not found"}

        provider = self._get_provider(acc["provider"], acc["account_id"])

        original_email: Email | None = None
        if thread_id:
            thread_emails = await provider.get_thread(thread_id)
            if thread_emails:
                for e in thread_emails:
                    if e.sender.address.lower() == account_id.lower():
                        original_email = e
                        break
        elif follow_up_id:
            original_email = await self._get_email_from_follow_up(
                follow_up_id, provider, account_id
            )

        if not original_email:
            return {"error": "Could not find original email to follow up on"}

        recipients = [r.address for r in original_email.to_recipients]
        if not recipients:
            return {"error": "Original email has no recipients"}

        days_waiting = 0
        if original_email.received_at:
            sent_naive = original_email.received_at
            if sent_naive.tzinfo:
                sent_naive = sent_naive.replace(tzinfo=None)
            days_waiting = (datetime.utcnow() - sent_naive).days

        style_examples = ""
        if include_style and self._ai_service:
            style_examples = await self._get_style_examples(provider)

        open_items = await self._get_open_items(recipients[0])

        draft_body = await self._generate_draft(
            prompt_template=FOLLOW_UP_PROMPT,
            subject=original_email.subject or "(no subject)",
            recipients=", ".join(recipients),
            date=(
                original_email.received_at.strftime("%B %d, %Y")
                if original_email.received_at else "unknown"
            ),
            body=original_email.body_text or original_email.snippet or "",
            days_waiting=str(days_waiting),
            style_examples=style_examples,
            open_items=open_items,
            instructions=instructions or "Write a polite follow-up.",
        )

        if not draft_body:
            return {"error": "Failed to generate draft"}

        subject = original_email.subject or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        result: dict[str, Any] = {
            "draft_body": draft_body,
            "subject": subject,
            "to": recipients,
            "account": account_id,
            "days_waiting": days_waiting,
            "original_subject": original_email.subject,
        }

        if create_draft:
            try:
                orig_thread_id: str | None = None
                if original_email.provider_ref:
                    orig_thread_id = original_email.provider_ref.thread_id

                draft_result = await provider.create_draft(
                    to=recipients,
                    subject=subject,
                    body=draft_body,
                    thread_id=orig_thread_id,
                )
                result["draft_id"] = draft_result.get("id")
                result["draft_created"] = True
            except Exception as e:
                logger.warning("Failed to create draft", error=str(e))
                result["draft_created"] = False
                result["draft_error"] = str(e)

        return result

    async def draft_new_email(
        self,
        to: list[str],
        subject: str,
        context: str,
        account_id: str | None = None,
        instructions: str | None = None,
        include_style: bool = True,
        create_draft: bool = False,
    ) -> dict[str, Any]:
        """Draft a new email with context.

        Args:
            to: Recipient email addresses
            subject: Email subject
            context: Context/purpose of the email
            account_id: Email account to use
            instructions: Specific drafting instructions
            include_style: Whether to match user's style
            create_draft: Whether to create draft in provider

        Returns:
            Draft content and metadata
        """
        if not account_id:
            accounts = self._get_email_accounts()
            for found_acc in accounts:
                if self._is_authorized(found_acc["provider"], found_acc["account_id"]):
                    account_id = found_acc["account_id"]
                    break

        if not account_id:
            return {"error": "No authorized email account available"}

        acc = self._find_account(account_id)
        if not acc:
            return {"error": f"Account {account_id} not found"}

        provider = self._get_provider(acc["provider"], acc["account_id"])

        style_examples: str = ""
        if include_style and self._ai_service:
            style_examples = await self._get_style_examples(provider)

        open_items: str = ""
        if to:
            open_items = await self._get_open_items(to[0])

        draft_body = await self._generate_draft(
            prompt_template=DRAFT_PROMPT,
            context=context,
            thread_history="(New email - no prior thread)",
            style_examples=style_examples,
            open_items=open_items,
            instructions=instructions or f"Write an email about: {context}",
        )

        if not draft_body:
            return {"error": "Failed to generate draft"}

        result: dict[str, Any] = {
            "draft_body": draft_body,
            "subject": subject,
            "to": to,
            "account": account_id,
        }

        if create_draft:
            try:
                draft_result = await provider.create_draft(
                    to=to,
                    subject=subject,
                    body=draft_body,
                )
                result["draft_id"] = draft_result.get("id")
                result["draft_created"] = True
            except Exception as e:
                logger.warning("Failed to create draft", error=str(e))
                result["draft_created"] = False
                result["draft_error"] = str(e)

        return result

    async def _generate_draft(
        self,
        prompt_template: str,
        **kwargs: Any,
    ) -> str | None:
        """Generate draft using AI."""
        if not self._ai_service:
            return self._generate_simple_draft(**kwargs)

        from executive_copilot.services.ai import AITextRequest

        try:
            prompt = prompt_template.format(**kwargs)
            result = await self._ai_service.generate_text(
                AITextRequest(
                    input=prompt,
                    instructions="You are a professional email assistant. Be concise.",
                    max_output_tokens=1000,
                    temperature=0.7,
                )
            )
            text: str | None = result.text.strip() if result.text else None
            return text
        except Exception as e:
            logger.warning("AI draft generation failed", error=str(e))
            return self._generate_simple_draft(**kwargs)

    def _generate_simple_draft(self, **kwargs: Any) -> str:
        """Generate a simple template-based draft when AI is unavailable."""
        instructions = kwargs.get("instructions", "")

        if "days_waiting" in kwargs:
            days = kwargs.get("days_waiting", "several")
            subject = kwargs.get("subject", "my previous email")
            return (
                f"Hi,\n\n"
                f"I wanted to follow up on {subject} that I sent {days} days ago. "
                f"I'd appreciate any update when you have a chance.\n\n"
                f"Thanks!"
            )

        context = kwargs.get("context", "")
        return (
            f"Hi,\n\n"
            f"[Draft regarding: {context}]\n\n"
            f"[Instructions: {instructions}]\n\n"
            f"Please complete this draft with the appropriate content.\n\n"
            f"Thanks!"
        )

    def _format_thread(self, emails: list[Email]) -> str:
        """Format email thread for context."""
        parts = []
        for email in emails[-5:]:
            sender = email.sender.display_name or email.sender.address
            date = email.received_at.strftime("%b %d") if email.received_at else "unknown"
            body = email.body_text or email.snippet or ""
            body = body[:1000]

            parts.append(f"From: {sender} ({date})\n{body}")

        return "\n\n---\n\n".join(parts)

    async def _get_style_examples(
        self,
        provider: GmailProvider | OutlookProvider,
        limit: int = 5,
    ) -> str:
        """Get examples of user's writing style from sent emails."""
        try:
            if hasattr(provider, "get_emails"):
                is_gmail = "gmail" in str(type(provider)).lower()
                query = "in:sent" if is_gmail else "folder:sentitems"
                sent_emails = await provider.get_emails(max_results=limit, query=query)
            else:
                return "(Style examples unavailable)"

            if not sent_emails:
                return "(No recent sent emails for style analysis)"

            examples = []
            for email in sent_emails[:limit]:
                body = email.body_text or email.snippet or ""
                body = body[:500]
                if body:
                    examples.append(body)

            if not examples:
                return "(No content available for style analysis)"

            return "\n\n---\n\n".join(examples[:3])

        except Exception as e:
            logger.warning("Failed to get style examples", error=str(e))
            return "(Style analysis unavailable)"

    async def _get_open_items(self, person_email: str) -> str:
        """Get open commitments, delegations, and follow-ups with a person."""
        items = []

        try:
            async with self.db_manager.session() as session:
                commitment_repo = CommitmentRepository(session)
                commitments = await commitment_repo.get_by_person(
                    person_email, include_completed=False
                )
                for c in commitments[:3]:
                    is_mine = c.direction.value == "i_promised"
                    direction = "I promised" if is_mine else "They promised"
                    items.append(f"- {direction}: {c.title}")

                delegation_repo = DelegationRepository(session)
                delegations = await delegation_repo.get_by_assignee(
                    person_email, include_completed=False
                )
                for d in delegations[:3]:
                    items.append(f"- Delegated to them: {d.title}")

                follow_up_repo = FollowUpRepository(session)
                follow_ups = await follow_up_repo.get_by_person(
                    person_email, include_completed=False
                )
                for f in follow_ups[:3]:
                    items.append(f"- Follow-up: {f.title}")

        except Exception as e:
            logger.warning("Failed to get open items", error=str(e))

        if not items:
            return "(No open items with this person)"

        return "\n".join(items)

    async def _get_email_from_follow_up(
        self,
        follow_up_id: str,
        provider: GmailProvider | OutlookProvider,
        account_id: str,
    ) -> Email | None:
        """Get original email from a follow-up item."""
        from uuid import UUID

        try:
            fup_uuid = UUID(follow_up_id)
            async with self.db_manager.session() as session:
                repo = FollowUpRepository(session)
                follow_up = await repo.get_by_id(fup_uuid)

                if follow_up and follow_up.source_id:
                    thread_id = str(follow_up.source_id)
                    emails = await provider.get_thread(thread_id)
                    if emails:
                        for e in emails:
                            if e.sender.address.lower() == account_id.lower():
                                return e
        except Exception as e:
            logger.warning("Failed to get email from follow-up", error=str(e))

        return None
