"""MCP server implementation using FastMCP with multi-account aggregation."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastmcp import FastMCP

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.config import AppConfig
from executive_copilot.logging import get_logger
from executive_copilot.providers.calendar.google import GoogleCalendarProvider
from executive_copilot.providers.calendar.outlook import OutlookCalendarProvider
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider
from executive_copilot.providers.task.asana import AsanaProvider

logger = get_logger(__name__)


def create_mcp_server(
    config: AppConfig,
    credential_manager: CredentialManager | None = None,
) -> FastMCP:
    """Create and configure the MCP server with multi-account aggregation."""
    mcp = FastMCP(
        name=config.mcp.name,
        version=config.mcp.version,
    )

    if credential_manager is None:
        credential_manager = CredentialManager(config.config_dir / ".credentials")

    # --- Helper functions to get provider accounts ---

    def get_email_accounts() -> list[dict[str, str]]:
        """Get all configured email accounts with provider info."""
        accounts = []
        for org in config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "email":
                    accounts.append({
                        "account_id": pa.account_id,
                        "provider": pa.provider_name,
                        "organization": org.name,
                        "org_type": org.organization_type,
                    })
        return accounts

    def get_calendar_accounts() -> list[dict[str, str]]:
        """Get all configured calendar accounts with provider info."""
        accounts = []
        seen = set()
        for org in config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "calendar":
                    key = (pa.account_id, pa.provider_name)
                    if key not in seen:
                        seen.add(key)
                        accounts.append({
                            "account_id": pa.account_id,
                            "provider": pa.provider_name,
                            "organization": org.name,
                            "org_type": org.organization_type,
                        })
        return accounts

    def get_task_accounts() -> list[dict[str, str]]:
        """Get all configured task accounts with provider info."""
        accounts = []
        seen = set()
        for org in config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "task":
                    key = (pa.account_id, pa.provider_name)
                    if key not in seen:
                        seen.add(key)
                        accounts.append({
                            "account_id": pa.account_id,
                            "provider": pa.provider_name,
                            "organization": org.name,
                            "org_type": org.organization_type,
                        })
        return accounts

    def is_account_authorized(provider_type: str, provider_name: str, account_id: str) -> bool:
        """Check if an account has valid credentials."""
        cred = credential_manager.get_credential(provider_type, provider_name, account_id)
        return cred is not None and cred.get("access_token") is not None

    def get_email_provider(provider_name: str, account_id: str) -> GmailProvider | OutlookProvider:
        """Get email provider instance."""
        if provider_name == "gmail":
            return GmailProvider(credential_manager, account_id)
        elif provider_name == "outlook":
            return OutlookProvider(credential_manager, account_id)
        raise ValueError(f"Unknown email provider: {provider_name}")

    def get_calendar_provider(provider_name: str, account_id: str) -> GoogleCalendarProvider | OutlookCalendarProvider:
        """Get calendar provider instance."""
        if provider_name == "google":
            return GoogleCalendarProvider(credential_manager, account_id)
        elif provider_name == "outlook":
            return OutlookCalendarProvider(credential_manager, account_id)
        raise ValueError(f"Unknown calendar provider: {provider_name}")

    def get_task_provider(provider_name: str, account_id: str) -> AsanaProvider:
        """Get task provider instance."""
        if provider_name == "asana":
            return AsanaProvider(credential_manager, account_id)
        raise ValueError(f"Unknown task provider: {provider_name}")

    # --- Email Tools ---

    @mcp.tool()
    async def get_all_unread_emails(limit_per_account: int = 10) -> dict[str, Any]:
        """Get unread emails from ALL authorized email accounts.

        This aggregates unread emails across Gmail and Outlook accounts,
        organized by organization.

        Args:
            limit_per_account: Maximum emails per account (default: 10)

        Returns:
            Aggregated unread emails across all accounts with organization grouping
        """
        accounts = get_email_accounts()
        results: dict[str, Any] = {
            "total_unread": 0,
            "by_organization": {},
            "emails": [],
            "errors": [],
        }

        async def fetch_unread(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("email", acc["provider"], acc["account_id"]):
                return acc, [], f"Not authorized: {acc['account_id']}"
            try:
                provider = get_email_provider(acc["provider"], acc["account_id"])
                emails = await provider.get_unread(max_results=limit_per_account)
                return acc, emails, None
            except Exception as e:
                return acc, [], str(e)

        tasks = [fetch_unread(acc) for acc in accounts]
        responses = await asyncio.gather(*tasks)

        for acc, emails, error in responses:
            org_name = acc["organization"]
            if org_name not in results["by_organization"]:
                results["by_organization"][org_name] = {
                    "count": 0,
                    "accounts": [],
                }

            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
            else:
                results["by_organization"][org_name]["count"] += len(emails)
                results["by_organization"][org_name]["accounts"].append({
                    "account": acc["account_id"],
                    "provider": acc["provider"],
                    "count": len(emails),
                })
                results["total_unread"] += len(emails)

                for e in emails:
                    results["emails"].append({
                        "account": acc["account_id"],
                        "organization": org_name,
                        "subject": e.subject,
                        "from": e.sender.address,
                        "from_name": e.sender.display_name,
                        "received_at": e.received_at.isoformat(),
                        "snippet": e.snippet,
                        "has_attachments": e.has_attachments,
                    })

        results["emails"].sort(key=lambda x: x["received_at"], reverse=True)
        return results

    @mcp.tool()
    async def get_inbox(
        account: str | None = None,
        limit: int = 20,
        unread_only: bool = False,
    ) -> dict[str, Any]:
        """Get inbox emails from a specific account or all accounts.

        Args:
            account: Email account (e.g., user@example.com). If not specified, gets from all accounts.
            limit: Maximum emails to return per account (default: 20)
            unread_only: Only return unread emails

        Returns:
            Inbox emails, aggregated if no account specified
        """
        accounts = get_email_accounts()

        if account:
            acc = next((a for a in accounts if a["account_id"] == account), None)
            if not acc:
                return {"error": f"Account {account} not found in configuration"}
            accounts = [acc]

        results: dict[str, Any] = {
            "total": 0,
            "by_account": {},
            "emails": [],
            "errors": [],
        }

        async def fetch_inbox(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("email", acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                provider = get_email_provider(acc["provider"], acc["account_id"])
                if unread_only:
                    emails = await provider.get_unread(max_results=limit)
                else:
                    emails = await provider.get_inbox(max_results=limit)
                return acc, emails, None
            except Exception as e:
                return acc, [], str(e)

        tasks = [fetch_inbox(acc) for acc in accounts]
        responses = await asyncio.gather(*tasks)

        for acc, emails, error in responses:
            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
            else:
                results["by_account"][acc["account_id"]] = {
                    "provider": acc["provider"],
                    "organization": acc["organization"],
                    "count": len(emails),
                }
                results["total"] += len(emails)

                for e in emails:
                    results["emails"].append({
                        "account": acc["account_id"],
                        "organization": acc["organization"],
                        "subject": e.subject,
                        "from": e.sender.address,
                        "from_name": e.sender.display_name,
                        "received_at": e.received_at.isoformat(),
                        "snippet": e.snippet,
                        "is_read": e.is_read,
                        "is_starred": e.is_starred,
                        "has_attachments": e.has_attachments,
                    })

        results["emails"].sort(key=lambda x: x["received_at"], reverse=True)
        return results

    @mcp.tool()
    async def search_all_emails(
        query: str,
        limit_per_account: int = 10,
    ) -> dict[str, Any]:
        """Search emails across ALL authorized accounts.

        Args:
            query: Search query (Gmail syntax for Gmail, KQL for Outlook)
            limit_per_account: Maximum results per account

        Returns:
            Matching emails from all accounts
        """
        accounts = get_email_accounts()
        results: dict[str, Any] = {
            "query": query,
            "total": 0,
            "emails": [],
            "errors": [],
        }

        async def search_account(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("email", acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                provider = get_email_provider(acc["provider"], acc["account_id"])
                emails = await provider.search(query, max_results=limit_per_account)
                return acc, emails, None
            except Exception as e:
                return acc, [], str(e)

        tasks = [search_account(acc) for acc in accounts]
        responses = await asyncio.gather(*tasks)

        for acc, emails, error in responses:
            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
            else:
                results["total"] += len(emails)
                for e in emails:
                    results["emails"].append({
                        "account": acc["account_id"],
                        "organization": acc["organization"],
                        "subject": e.subject,
                        "from": e.sender.address,
                        "from_name": e.sender.display_name,
                        "received_at": e.received_at.isoformat(),
                        "snippet": e.snippet,
                        "is_read": e.is_read,
                    })

        results["emails"].sort(key=lambda x: x["received_at"], reverse=True)
        return results

    @mcp.tool()
    async def get_emails_from_sender(
        sender: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get emails from a specific sender across all accounts.

        Args:
            sender: Sender email address or domain
            limit: Maximum total results

        Returns:
            Emails from the sender across all accounts
        """
        return await search_all_emails(query=f"from:{sender}", limit_per_account=limit)

    @mcp.tool()
    async def get_email_summary() -> dict[str, Any]:
        """Get a summary of email status across all accounts.

        Returns:
            Summary with unread counts per account and organization
        """
        accounts = get_email_accounts()
        results: dict[str, Any] = {
            "total_unread": 0,
            "by_organization": {},
            "accounts": [],
        }

        async def get_summary(acc: dict[str, str]) -> tuple[dict[str, str], int, str | None]:
            if not is_account_authorized("email", acc["provider"], acc["account_id"]):
                return acc, 0, "Not authorized"
            try:
                provider = get_email_provider(acc["provider"], acc["account_id"])
                unread = await provider.get_unread(max_results=100)
                return acc, len(unread), None
            except Exception as e:
                return acc, 0, str(e)

        tasks = [get_summary(acc) for acc in accounts]
        responses = await asyncio.gather(*tasks)

        for acc, count, error in responses:
            org_name = acc["organization"]
            if org_name not in results["by_organization"]:
                results["by_organization"][org_name] = 0

            account_info = {
                "account": acc["account_id"],
                "provider": acc["provider"],
                "organization": org_name,
                "authorized": error is None,
            }

            if error:
                account_info["error"] = error
                account_info["unread"] = 0
            else:
                account_info["unread"] = count
                results["by_organization"][org_name] += count
                results["total_unread"] += count

            results["accounts"].append(account_info)

        return results

    @mcp.tool()
    async def get_email_thread(
        thread_id: str,
        account: str,
    ) -> dict[str, Any]:
        """Get all emails in a conversation thread.

        Args:
            thread_id: Thread/conversation ID from the email provider
            account: Email account that contains this thread

        Returns:
            All emails in the thread in chronological order
        """
        accounts = get_email_accounts()
        acc = next((a for a in accounts if a["account_id"] == account), None)
        if not acc:
            return {"error": f"Account {account} not found"}

        if not is_account_authorized("email", acc["provider"], acc["account_id"]):
            return {"error": f"Account {account} is not authorized"}

        try:
            provider = get_email_provider(acc["provider"], acc["account_id"])
            emails = await provider.get_thread(thread_id)

            return {
                "thread_id": thread_id,
                "account": account,
                "email_count": len(emails),
                "emails": [
                    {
                        "subject": e.subject,
                        "from": e.sender.address,
                        "from_name": e.sender.display_name,
                        "to": [r.address for r in e.to_recipients],
                        "received_at": e.received_at.isoformat(),
                        "body_text": e.body_text,
                        "body_html": e.body_html,
                        "snippet": e.snippet,
                        "is_read": e.is_read,
                        "has_attachments": e.has_attachments,
                    }
                    for e in emails
                ],
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def send_email(
        to: list[str],
        subject: str,
        body: str,
        account: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send an email from one of your accounts.

        Args:
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            account: Account to send from. If not specified, uses first available.
            cc: CC recipients
            bcc: BCC recipients

        Returns:
            Sent email details
        """
        accounts = get_email_accounts()
        if not accounts:
            return {"error": "No email accounts configured"}

        if account:
            acc = next((a for a in accounts if a["account_id"] == account), None)
            if not acc:
                return {"error": f"Account {account} not found"}
        else:
            # Use first authorized account
            acc = None
            for a in accounts:
                if is_account_authorized("email", a["provider"], a["account_id"]):
                    acc = a
                    break
            if not acc:
                return {"error": "No authorized email accounts available"}

        if not is_account_authorized("email", acc["provider"], acc["account_id"]):
            return {"error": f"Account {acc['account_id']} is not authorized"}

        try:
            provider = get_email_provider(acc["provider"], acc["account_id"])
            sent = await provider.send_email(
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
            )

            return {
                "success": True,
                "account": acc["account_id"],
                "to": to,
                "cc": cc,
                "subject": subject,
                "sent_at": sent.received_at.isoformat() if sent.received_at else None,
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def create_email_draft(
        to: list[str],
        subject: str,
        body: str,
        account: str | None = None,
        cc: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an email draft.

        Args:
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            account: Account to create draft in. If not specified, uses first available.
            cc: CC recipients

        Returns:
            Draft details including draft ID
        """
        accounts = get_email_accounts()
        if not accounts:
            return {"error": "No email accounts configured"}

        if account:
            acc = next((a for a in accounts if a["account_id"] == account), None)
            if not acc:
                return {"error": f"Account {account} not found"}
        else:
            acc = None
            for a in accounts:
                if is_account_authorized("email", a["provider"], a["account_id"]):
                    acc = a
                    break
            if not acc:
                return {"error": "No authorized email accounts available"}

        if not is_account_authorized("email", acc["provider"], acc["account_id"]):
            return {"error": f"Account {acc['account_id']} is not authorized"}

        try:
            provider = get_email_provider(acc["provider"], acc["account_id"])
            draft = await provider.create_draft(
                to=to,
                subject=subject,
                body=body,
                cc=cc,
            )

            return {
                "success": True,
                "account": acc["account_id"],
                "draft_id": draft.get("id"),
                "to": to,
                "subject": subject,
            }
        except Exception as e:
            return {"error": str(e)}

    # --- Response Drafting Tools ---

    @mcp.tool()
    async def draft_reply(
        thread_id: str,
        account: str,
        instructions: str | None = None,
        create_draft: bool = False,
    ) -> dict[str, Any]:
        """Draft a reply to an email thread with context awareness.

        Analyzes the thread history, matches your writing style from sent emails,
        and considers any open commitments/delegations with the recipient.

        Args:
            thread_id: Thread/conversation ID from the email
            account: Email account containing this thread
            instructions: Optional specific instructions (e.g., "politely decline",
                         "ask for more details", "confirm the meeting")
            create_draft: If True, saves the draft in your email account

        Returns:
            Draft body text and metadata. If create_draft=True, includes draft_id.
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.response_drafting import ResponseDraftingService

        db_manager = DatabaseManager(config.database)

        ai_service = None
        try:
            from executive_copilot.database import DatabaseManager
            from executive_copilot.providers.base import ProviderRegistry
            from executive_copilot.services.ai import AIService
            from executive_copilot.services.container import ServiceContainer

            registry = ProviderRegistry()
            container = ServiceContainer(config, db_manager, registry)
            ai_service = AIService(container)
        except Exception:
            pass

        service = ResponseDraftingService(
            config=config,
            credential_manager=credential_manager,
            db_manager=db_manager,
            ai_service=ai_service,
        )

        result = await service.draft_reply(
            thread_id=thread_id,
            account_id=account,
            instructions=instructions,
            include_style=True,
            create_draft=create_draft,
        )

        await db_manager.close()
        return result

    @mcp.tool()
    async def draft_follow_up(
        thread_id: str | None = None,
        follow_up_id: str | None = None,
        account: str | None = None,
        instructions: str | None = None,
        create_draft: bool = False,
    ) -> dict[str, Any]:
        """Draft a follow-up email for a message awaiting response.

        Use this when you sent an email and haven't received a reply.
        Generates a polite follow-up that references the original message.

        Args:
            thread_id: Thread ID of the original conversation
            follow_up_id: ID of a follow-up item from get_follow_ups
            account: Email account (uses first available if not specified)
            instructions: Optional instructions (e.g., "more urgent tone",
                         "mention the deadline")
            create_draft: If True, saves the draft in your email account

        Returns:
            Draft body text, days since original email, and metadata
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.response_drafting import ResponseDraftingService

        db_manager = DatabaseManager(config.database)

        ai_service = None
        try:
            from executive_copilot.providers.base import ProviderRegistry
            from executive_copilot.services.ai import AIService
            from executive_copilot.services.container import ServiceContainer

            registry = ProviderRegistry()
            container = ServiceContainer(config, db_manager, registry)
            ai_service = AIService(container)
        except Exception:
            pass

        service = ResponseDraftingService(
            config=config,
            credential_manager=credential_manager,
            db_manager=db_manager,
            ai_service=ai_service,
        )

        result = await service.draft_follow_up(
            thread_id=thread_id,
            follow_up_id=follow_up_id,
            account_id=account,
            instructions=instructions,
            include_style=True,
            create_draft=create_draft,
        )

        await db_manager.close()
        return result

    @mcp.tool()
    async def draft_email(
        to: list[str],
        subject: str,
        context: str,
        account: str | None = None,
        instructions: str | None = None,
        create_draft: bool = False,
    ) -> dict[str, Any]:
        """Draft a new email with AI assistance.

        Creates an email draft based on context and instructions,
        matching your writing style from previous emails.

        Args:
            to: Recipient email addresses
            subject: Email subject line
            context: Purpose/context of the email (e.g., "schedule a call to discuss
                    Q3 results", "thank them for the meeting yesterday")
            account: Email account to use (uses first available if not specified)
            instructions: Specific drafting instructions (e.g., "keep it brief",
                         "formal tone", "mention the attached document")
            create_draft: If True, saves the draft in your email account

        Returns:
            Draft body text and metadata. If create_draft=True, includes draft_id.
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.response_drafting import ResponseDraftingService

        db_manager = DatabaseManager(config.database)

        ai_service = None
        try:
            from executive_copilot.providers.base import ProviderRegistry
            from executive_copilot.services.ai import AIService
            from executive_copilot.services.container import ServiceContainer

            registry = ProviderRegistry()
            container = ServiceContainer(config, db_manager, registry)
            ai_service = AIService(container)
        except Exception:
            pass

        service = ResponseDraftingService(
            config=config,
            credential_manager=credential_manager,
            db_manager=db_manager,
            ai_service=ai_service,
        )

        result = await service.draft_new_email(
            to=to,
            subject=subject,
            context=context,
            account_id=account,
            instructions=instructions,
            include_style=True,
            create_draft=create_draft,
        )

        await db_manager.close()
        return result

    # --- Calendar Tools ---

    @mcp.tool()
    async def get_today_events() -> dict[str, Any]:
        """Get today's calendar events from ALL authorized accounts.

        Returns:
            Today's events aggregated from all calendar accounts
        """
        accounts = get_calendar_accounts()
        results: dict[str, Any] = {
            "date": datetime.now(UTC).date().isoformat(),
            "total_events": 0,
            "by_organization": {},
            "events": [],
            "errors": [],
        }

        async def fetch_today(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("calendar", acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                provider = get_calendar_provider(acc["provider"], acc["account_id"])
                events = await provider.get_today_events()
                return acc, events, None
            except Exception as e:
                return acc, [], str(e)

        tasks = [fetch_today(acc) for acc in accounts]
        responses = await asyncio.gather(*tasks)

        for acc, events, error in responses:
            org_name = acc["organization"]
            if org_name not in results["by_organization"]:
                results["by_organization"][org_name] = {"count": 0, "accounts": []}

            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
            else:
                results["by_organization"][org_name]["count"] += len(events)
                results["by_organization"][org_name]["accounts"].append(acc["account_id"])
                results["total_events"] += len(events)

                for e in events:
                    results["events"].append({
                        "account": acc["account_id"],
                        "organization": org_name,
                        "title": e.title,
                        "start": e.start_time.isoformat(),
                        "end": e.end_time.isoformat(),
                        "is_all_day": e.is_all_day,
                        "location": e.location.display_name if e.location else None,
                        "conference_url": e.conference_url,
                        "attendees": len(e.attendees),
                        "status": e.status.value,
                    })

        results["events"].sort(key=lambda x: x["start"])
        return results

    @mcp.tool()
    async def get_upcoming_events(hours: int = 24) -> dict[str, Any]:
        """Get upcoming calendar events from all accounts.

        Args:
            hours: Hours to look ahead (default: 24)

        Returns:
            Upcoming events aggregated from all calendar accounts
        """
        accounts = get_calendar_accounts()
        results: dict[str, Any] = {
            "looking_ahead_hours": hours,
            "total_events": 0,
            "events": [],
            "errors": [],
        }

        async def fetch_upcoming(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("calendar", acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                provider = get_calendar_provider(acc["provider"], acc["account_id"])
                events = await provider.get_upcoming_events(hours=hours, limit=20)
                return acc, events, None
            except Exception as e:
                return acc, [], str(e)

        tasks = [fetch_upcoming(acc) for acc in accounts]
        responses = await asyncio.gather(*tasks)

        for acc, events, error in responses:
            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
            else:
                results["total_events"] += len(events)
                for e in events:
                    results["events"].append({
                        "account": acc["account_id"],
                        "organization": acc["organization"],
                        "title": e.title,
                        "start": e.start_time.isoformat(),
                        "end": e.end_time.isoformat(),
                        "duration_minutes": e.duration_minutes,
                        "is_all_day": e.is_all_day,
                        "location": e.location.display_name if e.location else None,
                        "conference_url": e.conference_url,
                        "attendees": len(e.attendees),
                    })

        results["events"].sort(key=lambda x: x["start"])
        return results

    @mcp.tool()
    async def get_week_events(days: int = 7) -> dict[str, Any]:
        """Get calendar events for the next N days from all accounts.

        Args:
            days: Number of days to look ahead (default: 7)

        Returns:
            Events for the week grouped by day
        """
        accounts = get_calendar_accounts()
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)

        results: dict[str, Any] = {
            "period": f"{start.date().isoformat()} to {end.date().isoformat()}",
            "total_events": 0,
            "by_day": {},
            "errors": [],
        }

        from executive_copilot.providers.calendar.interface import EventQuery

        async def fetch_week(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("calendar", acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                provider = get_calendar_provider(acc["provider"], acc["account_id"])
                query = EventQuery(start_time=start, end_time=end, limit=100)
                events, _ = await provider.get_events(query)
                return acc, events, None
            except Exception as e:
                return acc, [], str(e)

        tasks = [fetch_week(acc) for acc in accounts]
        responses = await asyncio.gather(*tasks)

        all_events = []
        for acc, events, error in responses:
            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
            else:
                for e in events:
                    all_events.append({
                        "account": acc["account_id"],
                        "organization": acc["organization"],
                        "title": e.title,
                        "start": e.start_time,
                        "end": e.end_time,
                        "is_all_day": e.is_all_day,
                        "location": e.location.display_name if e.location else None,
                        "conference_url": e.conference_url,
                    })

        all_events.sort(key=lambda x: x["start"])

        for event in all_events:
            day = event["start"].date().isoformat()
            if day not in results["by_day"]:
                results["by_day"][day] = []
            results["by_day"][day].append({
                "account": event["account"],
                "organization": event["organization"],
                "title": event["title"],
                "start": event["start"].isoformat(),
                "end": event["end"].isoformat(),
                "is_all_day": event["is_all_day"],
                "location": event["location"],
                "conference_url": event["conference_url"],
            })
            results["total_events"] += 1

        return results

    @mcp.tool()
    async def list_calendars() -> dict[str, Any]:
        """List all calendars from all authorized accounts.

        Returns:
            List of calendars grouped by account
        """
        accounts = get_calendar_accounts()
        results: dict[str, Any] = {
            "total_calendars": 0,
            "by_account": {},
            "errors": [],
        }

        async def fetch_calendars(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("calendar", acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                provider = get_calendar_provider(acc["provider"], acc["account_id"])
                calendars = await provider.list_calendars()
                return acc, calendars, None
            except Exception as e:
                return acc, [], str(e)

        tasks = [fetch_calendars(acc) for acc in accounts]
        responses = await asyncio.gather(*tasks)

        for acc, calendars, error in responses:
            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
            else:
                results["by_account"][acc["account_id"]] = {
                    "provider": acc["provider"],
                    "organization": acc["organization"],
                    "calendars": [
                        {
                            "id": c.id,
                            "name": c.name,
                            "is_primary": c.is_primary,
                            "can_edit": c.can_edit,
                        }
                        for c in calendars
                    ],
                }
                results["total_calendars"] += len(calendars)

        return results

    # --- Task Tools ---

    def serialize_task(task: Any, account: dict[str, str] | None = None) -> dict[str, Any]:
        """Serialize a task domain model for MCP responses."""
        data = {
            "id": str(task.id),
            "provider_id": task.provider_ref.external_id if task.provider_ref else None,
            "url": task.provider_ref.external_url if task.provider_ref else None,
            "title": task.title,
            "description": task.description,
            "status": task.status.value,
            "priority": task.priority.value,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "project_id": task.project_id,
            "project_name": task.project_name,
            "section_id": task.section_id,
            "section_name": task.section_name,
            "assignee_name": task.assignee_name,
            "assignee_email": task.assignee_email,
            "tags": task.tags,
        }
        if account:
            data.update({
                "account": account["account_id"],
                "provider": account["provider"],
                "organization": account["organization"],
            })
        return data

    @mcp.tool()
    async def get_my_tasks(
        account: str | None = None,
        include_completed: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get assigned tasks from Asana accounts.

        Args:
            account: Task account to query. If omitted, queries all authorized task accounts.
            include_completed: Include completed tasks.
            limit: Maximum tasks per account.

        Returns:
            Assigned tasks grouped across configured task accounts.
        """
        accounts = get_task_accounts()
        if account:
            accounts = [acc for acc in accounts if acc["account_id"] == account]
            if not accounts:
                return {"error": f"Task account {account} not found in configuration"}

        results: dict[str, Any] = {"total": 0, "tasks": [], "by_account": {}, "errors": []}

        async def fetch_tasks(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("task", acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                provider = get_task_provider(acc["provider"], acc["account_id"])
                provider_tasks = await provider.get_my_tasks(include_completed=include_completed)
                return acc, provider_tasks[:limit], None
            except Exception as e:
                return acc, [], str(e)

        responses = await asyncio.gather(*[fetch_tasks(acc) for acc in accounts])

        for acc, tasks, error in responses:
            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
                continue
            results["by_account"][acc["account_id"]] = {
                "provider": acc["provider"],
                "organization": acc["organization"],
                "count": len(tasks),
            }
            results["total"] += len(tasks)
            results["tasks"].extend(serialize_task(task, acc) for task in tasks)

        results["tasks"].sort(key=lambda x: (x["due_date"] is None, x["due_date"] or ""))
        return results

    @mcp.tool()
    async def list_task_projects(account: str | None = None) -> dict[str, Any]:
        """List Asana projects for configured task accounts.

        Args:
            account: Task account to query. If omitted, queries all authorized task accounts.

        Returns:
            Projects grouped by account.
        """
        accounts = get_task_accounts()
        if account:
            accounts = [acc for acc in accounts if acc["account_id"] == account]
            if not accounts:
                return {"error": f"Task account {account} not found in configuration"}

        results: dict[str, Any] = {"total_projects": 0, "by_account": {}, "errors": []}

        async def fetch_projects(acc: dict[str, str]) -> tuple[dict[str, str], list[Any], str | None]:
            if not is_account_authorized("task", acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                provider = get_task_provider(acc["provider"], acc["account_id"])
                projects = await provider.list_projects()
                return acc, projects, None
            except Exception as e:
                return acc, [], str(e)

        responses = await asyncio.gather(*[fetch_projects(acc) for acc in accounts])

        for acc, projects, error in responses:
            if error:
                results["errors"].append({"account": acc["account_id"], "error": error})
                continue
            results["by_account"][acc["account_id"]] = {
                "provider": acc["provider"],
                "organization": acc["organization"],
                "projects": [
                    {
                        "id": project.id,
                        "name": project.name,
                        "workspace_id": project.workspace_id,
                        "team_id": project.team_id,
                    }
                    for project in projects
                ],
            }
            results["total_projects"] += len(projects)

        return results

    @mcp.tool()
    async def create_asana_task(
        title: str,
        account: str | None = None,
        description: str | None = None,
        project_id: str | None = None,
        assignee_email: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        """Create a task in Asana.

        Args:
            title: Task title.
            account: Asana account identifier.
            description: Optional task notes.
            project_id: Optional Asana project GID.
            assignee_email: Optional assignee email.
            due_date: Optional due date in YYYY-MM-DD format.

        Returns:
            Created task details.
        """
        from executive_copilot.providers.task import NewTask

        task_accounts = get_task_accounts()
        if not task_accounts:
            return {"error": "No task accounts configured"}

        if account:
            acc = next((a for a in task_accounts if a["account_id"] == account), None)
            if not acc:
                return {"error": f"Task account {account} not found in configuration"}
        else:
            acc = task_accounts[0]
        if not is_account_authorized("task", acc["provider"], acc["account_id"]):
            return {"error": f"Task account {account} is not authorized"}

        parsed_due = None
        if due_date:
            parsed_due = datetime.combine(
                datetime.fromisoformat(due_date).date(),
                datetime.min.time(),
                tzinfo=UTC,
            )

        provider = get_task_provider(acc["provider"], acc["account_id"])
        task = await provider.create_task(
            NewTask(
                title=title,
                description=description,
                project_id=project_id,
                assignee_email=assignee_email,
                due_date=parsed_due,
            )
        )
        return {"task": serialize_task(task, acc)}

    @mcp.tool()
    async def complete_asana_task(
        task_id: str,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Mark an Asana task complete.

        Args:
            task_id: Asana task GID.
            account: Asana account identifier.

        Returns:
            Updated task details.
        """
        task_accounts = get_task_accounts()
        if not task_accounts:
            return {"error": "No task accounts configured"}

        if account:
            acc = next((a for a in task_accounts if a["account_id"] == account), None)
            if not acc:
                return {"error": f"Task account {account} not found in configuration"}
        else:
            acc = task_accounts[0]

        if not is_account_authorized("task", acc["provider"], acc["account_id"]):
            return {"error": f"Task account {account} is not authorized"}

        provider = get_task_provider(acc["provider"], acc["account_id"])
        task = await provider.complete_task(task_id)
        return {"task": serialize_task(task, acc)}

    # --- Follow-up Tools ---

    def serialize_follow_up(follow_up: Any) -> dict[str, Any]:
        """Serialize a follow-up domain model for MCP responses."""
        return {
            "id": str(follow_up.id),
            "title": follow_up.title,
            "description": follow_up.description,
            "context": follow_up.context,
            "status": follow_up.status.value,
            "priority": follow_up.priority.value,
            "due_date": follow_up.due_date.isoformat() if follow_up.due_date else None,
            "snoozed_until": (
                follow_up.snoozed_until.isoformat() if follow_up.snoozed_until else None
            ),
            "completed_at": (
                follow_up.completed_at.isoformat() if follow_up.completed_at else None
            ),
            "with_person_name": follow_up.with_person_name,
            "with_person_email": follow_up.with_person_email,
            "auto_detected": follow_up.auto_detected,
            "tags": follow_up.tags,
            "notes": follow_up.notes,
            "is_overdue": follow_up.is_overdue,
            "created_at": follow_up.created_at.isoformat(),
        }

    @mcp.tool()
    async def get_follow_ups(
        include_snoozed: bool = False,
        include_completed: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get follow-up items that need attention.

        Returns pending follow-ups ordered by due date and priority.
        Overdue items are highlighted.

        Args:
            include_snoozed: Include snoozed follow-ups (default: False)
            include_completed: Include completed follow-ups (default: False)
            limit: Maximum results (default: 50)

        Returns:
            Follow-up items with status summary
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories.follow_up import FollowUpRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = FollowUpRepository(session)
            follow_ups = await repo.get_pending(
                limit=limit,
                include_snoozed=include_snoozed,
            )

            if include_completed:
                from sqlalchemy import select

                from executive_copilot.database.tables import FollowUpTable
                from executive_copilot.models.communication import FollowUpStatus

                stmt = (
                    select(FollowUpTable)
                    .where(FollowUpTable.status == FollowUpStatus.COMPLETED.value)  # type: ignore[arg-type]
                    .order_by(FollowUpTable.completed_at.desc())  # type: ignore[union-attr]
                    .limit(limit)
                )
                result = await session.execute(stmt)
                completed_rows = result.scalars().all()
                completed = [repo._to_model(row) for row in completed_rows]
                follow_ups = follow_ups + completed

        await db_manager.close()

        overdue = [f for f in follow_ups if f.is_overdue]
        pending = [f for f in follow_ups if f.status.value == "pending" and not f.is_overdue]
        in_progress = [f for f in follow_ups if f.status.value == "in_progress"]

        return {
            "total": len(follow_ups),
            "summary": {
                "overdue": len(overdue),
                "pending": len(pending),
                "in_progress": len(in_progress),
            },
            "follow_ups": [serialize_follow_up(f) for f in follow_ups],
        }

    @mcp.tool()
    async def create_follow_up(
        title: str,
        description: str | None = None,
        due_date: str | None = None,
        priority: str = "medium",
        with_person_email: str | None = None,
        with_person_name: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new follow-up item.

        Use this to track items that need future attention, such as:
        - Waiting for a response from someone
        - Need to check back on a project status
        - Promised to follow up on a topic

        Args:
            title: Brief description of the follow-up
            description: Detailed notes about what needs to be done
            due_date: When to follow up (YYYY-MM-DD format)
            priority: Priority level (low, medium, high, urgent)
            with_person_email: Email of person to follow up with
            with_person_name: Name of person to follow up with
            tags: Tags for categorization

        Returns:
            Created follow-up details
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories.follow_up import FollowUpRepository
        from executive_copilot.models.communication import (
            FollowUp,
            FollowUpPriority,
            FollowUpStatus,
        )

        parsed_due = None
        if due_date:
            parsed_due = datetime.combine(
                datetime.fromisoformat(due_date).date(),
                datetime.min.time(),
                tzinfo=UTC,
            )

        try:
            priority_enum = FollowUpPriority(priority)
        except ValueError:
            return {"error": f"Invalid priority: {priority}. Use low, medium, high, or urgent."}

        follow_up = FollowUp(
            id=uuid4(),
            created_at=datetime.now(UTC),
            updated_at=None,
            title=title,
            description=description,
            status=FollowUpStatus.PENDING,
            priority=priority_enum,
            due_date=parsed_due,
            with_person_email=with_person_email,
            with_person_name=with_person_name,
            tags=tags or [],
            auto_detected=False,
        )

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = FollowUpRepository(session)
            created = await repo.create(follow_up)
            await session.commit()

        await db_manager.close()

        return {
            "success": True,
            "follow_up": serialize_follow_up(created),
        }

    @mcp.tool()
    async def update_follow_up(
        follow_up_id: str,
        status: str | None = None,
        priority: str | None = None,
        due_date: str | None = None,
        notes: str | None = None,
        snooze_until: str | None = None,
    ) -> dict[str, Any]:
        """Update a follow-up item.

        Use this to:
        - Mark a follow-up as completed
        - Change priority or due date
        - Snooze until a later date
        - Add notes

        Args:
            follow_up_id: ID of the follow-up to update
            status: New status (pending, in_progress, completed, cancelled, snoozed)
            priority: New priority (low, medium, high, urgent)
            due_date: New due date (YYYY-MM-DD format)
            notes: Additional notes to add
            snooze_until: Snooze until this date (YYYY-MM-DD format, sets status to snoozed)

        Returns:
            Updated follow-up details
        """
        from uuid import UUID

        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories.follow_up import FollowUpRepository
        from executive_copilot.models.communication import FollowUpPriority, FollowUpStatus

        try:
            fup_uuid = UUID(follow_up_id)
        except ValueError:
            return {"error": f"Invalid follow-up ID: {follow_up_id}"}

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = FollowUpRepository(session)
            follow_up = await repo.get_by_id(fup_uuid)

            if not follow_up:
                await db_manager.close()
                return {"error": f"Follow-up not found: {follow_up_id}"}

            updates: dict[str, Any] = {}

            if status:
                try:
                    new_status = FollowUpStatus(status)
                    updates["status"] = new_status
                    if new_status == FollowUpStatus.COMPLETED:
                        updates["completed_at"] = datetime.utcnow()
                except ValueError:
                    await db_manager.close()
                    return {"error": f"Invalid status: {status}"}

            if priority:
                try:
                    updates["priority"] = FollowUpPriority(priority)
                except ValueError:
                    await db_manager.close()
                    return {"error": f"Invalid priority: {priority}"}

            if due_date:
                updates["due_date"] = datetime.combine(
                    datetime.fromisoformat(due_date).date(),
                    datetime.min.time(),
                    tzinfo=UTC,
                )

            if notes:
                existing_notes = follow_up.notes or ""
                timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                updates["notes"] = f"{existing_notes}\n[{timestamp}] {notes}".strip()

            if snooze_until:
                updates["status"] = FollowUpStatus.SNOOZED
                updates["snoozed_until"] = datetime.combine(
                    datetime.fromisoformat(snooze_until).date(),
                    datetime.min.time(),
                    tzinfo=UTC,
                )

            if updates:
                updates["updated_at"] = datetime.utcnow()
                updated = follow_up.with_update(**updates)
                await repo.update(updated)
                await session.commit()
                follow_up = updated

        await db_manager.close()

        return {
            "success": True,
            "follow_up": serialize_follow_up(follow_up),
        }

    @mcp.tool()
    async def complete_follow_up(follow_up_id: str) -> dict[str, Any]:
        """Mark a follow-up as completed.

        Shorthand for update_follow_up with status=completed.

        Args:
            follow_up_id: ID of the follow-up to complete

        Returns:
            Updated follow-up details
        """
        return await update_follow_up(follow_up_id, status="completed")

    @mcp.tool()
    async def search_follow_ups(query: str, limit: int = 20) -> dict[str, Any]:
        """Search follow-ups by title or description.

        Args:
            query: Search term
            limit: Maximum results

        Returns:
            Matching follow-ups
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories.follow_up import FollowUpRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = FollowUpRepository(session)
            follow_ups = await repo.search(query, limit=limit)

        await db_manager.close()

        return {
            "query": query,
            "total": len(follow_ups),
            "follow_ups": [serialize_follow_up(f) for f in follow_ups],
        }

    @mcp.tool()
    async def get_overdue_follow_ups() -> dict[str, Any]:
        """Get all overdue follow-ups.

        Returns follow-ups that are past their due date and not completed.

        Returns:
            Overdue follow-ups ordered by due date
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories.follow_up import FollowUpRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = FollowUpRepository(session)
            follow_ups = await repo.get_overdue()

        await db_manager.close()

        return {
            "total": len(follow_ups),
            "follow_ups": [serialize_follow_up(f) for f in follow_ups],
        }

    @mcp.tool()
    async def get_follow_ups_with_person(
        person_email: str,
        include_completed: bool = False,
    ) -> dict[str, Any]:
        """Get follow-ups related to a specific person.

        Args:
            person_email: Email address of the person
            include_completed: Include completed follow-ups

        Returns:
            Follow-ups with this person
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories.follow_up import FollowUpRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = FollowUpRepository(session)
            follow_ups = await repo.get_by_person(
                person_email,
                include_completed=include_completed,
            )

        await db_manager.close()

        return {
            "person_email": person_email,
            "total": len(follow_ups),
            "follow_ups": [serialize_follow_up(f) for f in follow_ups],
        }

    # --- Commitment Tools ---

    def serialize_commitment(commitment: Any) -> dict[str, Any]:
        """Serialize a commitment domain model for MCP responses."""
        return {
            "id": str(commitment.id),
            "title": commitment.title,
            "description": commitment.description,
            "original_text": commitment.original_text,
            "direction": commitment.direction.value,
            "promiser_name": commitment.promiser_name,
            "promiser_email": commitment.promiser_email,
            "promisee_name": commitment.promisee_name,
            "promisee_email": commitment.promisee_email,
            "status": commitment.status.value,
            "due_date": commitment.due_date.isoformat() if commitment.due_date else None,
            "completed_at": commitment.completed_at.isoformat() if commitment.completed_at else None,
            "source_type": commitment.source_type,
            "source_date": commitment.source_date.isoformat() if commitment.source_date else None,
            "auto_detected": commitment.auto_detected,
            "detection_confidence": commitment.detection_confidence,
            "tags": commitment.tags,
            "is_overdue": commitment.is_overdue,
            "days_until_due": commitment.days_until_due,
            "created_at": commitment.created_at.isoformat(),
        }

    @mcp.tool()
    async def get_my_commitments(
        include_completed: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get commitments I made to others.

        Shows things I promised to do for other people, such as:
        - "I'll send you the report by Friday"
        - "I will review this and get back to you"
        - "Let me take care of that"

        Args:
            include_completed: Include completed commitments (default: False)
            limit: Maximum results (default: 50)

        Returns:
            My commitments with status summary
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import CommitmentRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = CommitmentRepository(session)
            commitments = await repo.get_my_commitments(
                include_completed=include_completed,
                limit=limit,
            )

        await db_manager.close()

        overdue = [c for c in commitments if c.is_overdue]
        pending = [c for c in commitments if c.status.value == "pending" and not c.is_overdue]

        return {
            "direction": "i_promised",
            "total": len(commitments),
            "summary": {
                "overdue": len(overdue),
                "pending": len(pending),
            },
            "commitments": [serialize_commitment(c) for c in commitments],
        }

    @mcp.tool()
    async def get_others_commitments(
        include_completed: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get commitments others made to me.

        Shows things other people promised to do for me, such as:
        - "John said he'll send the proposal Monday"
        - "She will review it by end of week"
        - "They promised to get back to us"

        Args:
            include_completed: Include completed commitments (default: False)
            limit: Maximum results (default: 50)

        Returns:
            Others' commitments with status summary
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import CommitmentRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = CommitmentRepository(session)
            commitments = await repo.get_others_commitments(
                include_completed=include_completed,
                limit=limit,
            )

        await db_manager.close()

        overdue = [c for c in commitments if c.is_overdue]
        pending = [c for c in commitments if c.status.value == "pending" and not c.is_overdue]

        return {
            "direction": "they_promised",
            "total": len(commitments),
            "summary": {
                "overdue": len(overdue),
                "pending": len(pending),
            },
            "commitments": [serialize_commitment(c) for c in commitments],
        }

    @mcp.tool()
    async def get_all_commitments(
        include_completed: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get all commitments in both directions.

        Shows both:
        - Things I promised to others (i_promised)
        - Things others promised to me (they_promised)

        Args:
            include_completed: Include completed commitments (default: False)
            limit: Maximum results per direction (default: 50)

        Returns:
            All commitments grouped by direction
        """
        my_commitments = await get_my_commitments(include_completed, limit)
        others_commitments = await get_others_commitments(include_completed, limit)

        return {
            "i_promised": my_commitments,
            "they_promised": others_commitments,
            "total": my_commitments["total"] + others_commitments["total"],
            "overdue_total": (
                my_commitments["summary"]["overdue"]
                + others_commitments["summary"]["overdue"]
            ),
        }

    @mcp.tool()
    async def get_overdue_commitments() -> dict[str, Any]:
        """Get all overdue commitments (both directions).

        Returns commitments past their due date that haven't been completed.

        Returns:
            Overdue commitments ordered by due date
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import CommitmentRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = CommitmentRepository(session)
            commitments = await repo.get_overdue()

        await db_manager.close()

        i_promised = [c for c in commitments if c.direction.value == "i_promised"]
        they_promised = [c for c in commitments if c.direction.value == "they_promised"]

        return {
            "total": len(commitments),
            "i_promised_count": len(i_promised),
            "they_promised_count": len(they_promised),
            "commitments": [serialize_commitment(c) for c in commitments],
        }

    @mcp.tool()
    async def complete_commitment(commitment_id: str) -> dict[str, Any]:
        """Mark a commitment as completed.

        Args:
            commitment_id: ID of the commitment to complete

        Returns:
            Updated commitment details
        """
        from uuid import UUID

        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import CommitmentRepository

        try:
            comm_uuid = UUID(commitment_id)
        except ValueError:
            return {"error": f"Invalid commitment ID: {commitment_id}"}

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = CommitmentRepository(session)
            commitment = await repo.get_by_id(comm_uuid)

            if not commitment:
                await db_manager.close()
                return {"error": f"Commitment not found: {commitment_id}"}

            updated = commitment.mark_completed()
            await repo.update(updated)
            await session.commit()

        await db_manager.close()

        return {
            "success": True,
            "commitment": serialize_commitment(updated),
        }

    @mcp.tool()
    async def create_commitment(
        title: str,
        direction: str,
        description: str | None = None,
        due_date: str | None = None,
        promiser_name: str | None = None,
        promiser_email: str | None = None,
        promisee_name: str | None = None,
        promisee_email: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new commitment manually.

        Use this to track a promise that wasn't auto-detected.

        Args:
            title: Brief description of the commitment
            direction: "i_promised" (I promised to others) or "they_promised" (others promised to me)
            description: Detailed notes
            due_date: When the commitment is due (YYYY-MM-DD format)
            promiser_name: Name of person who made the promise
            promiser_email: Email of person who made the promise
            promisee_name: Name of person the promise was made to
            promisee_email: Email of person the promise was made to
            tags: Tags for categorization

        Returns:
            Created commitment details
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import CommitmentRepository
        from executive_copilot.models.communication import (
            Commitment,
            CommitmentDirection,
            CommitmentStatus,
        )

        if direction not in ("i_promised", "they_promised"):
            return {"error": "direction must be 'i_promised' or 'they_promised'"}

        parsed_due = None
        if due_date:
            parsed_due = datetime.combine(
                datetime.fromisoformat(due_date).date(),
                datetime.min.time(),
                tzinfo=UTC,
            )

        commitment = Commitment(
            id=uuid4(),
            created_at=datetime.now(UTC),
            updated_at=None,
            title=title,
            description=description,
            direction=CommitmentDirection(direction),
            promiser_name=promiser_name,
            promiser_email=promiser_email,
            promisee_name=promisee_name,
            promisee_email=promisee_email,
            status=CommitmentStatus.PENDING,
            due_date=parsed_due,
            auto_detected=False,
            tags=tags or [],
        )

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = CommitmentRepository(session)
            created = await repo.create(commitment)
            await session.commit()

        await db_manager.close()

        return {
            "success": True,
            "commitment": serialize_commitment(created),
        }

    @mcp.tool()
    async def search_commitments(query: str, limit: int = 20) -> dict[str, Any]:
        """Search commitments by title, description, or original text.

        Args:
            query: Search term
            limit: Maximum results

        Returns:
            Matching commitments
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import CommitmentRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = CommitmentRepository(session)
            commitments = await repo.search(query, limit=limit)

        await db_manager.close()

        return {
            "query": query,
            "total": len(commitments),
            "commitments": [serialize_commitment(c) for c in commitments],
        }

    @mcp.tool()
    async def get_commitments_with_person(
        email: str,
        include_completed: bool = False,
    ) -> dict[str, Any]:
        """Get commitments involving a specific person.

        Args:
            email: Email address of the person
            include_completed: Include completed commitments

        Returns:
            Commitments with this person (both directions)
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import CommitmentRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = CommitmentRepository(session)
            commitments = await repo.get_by_person(email, include_completed=include_completed)

        await db_manager.close()

        i_promised = [c for c in commitments if c.direction.value == "i_promised"]
        they_promised = [c for c in commitments if c.direction.value == "they_promised"]

        return {
            "person_email": email,
            "total": len(commitments),
            "i_promised_count": len(i_promised),
            "they_promised_count": len(they_promised),
            "commitments": [serialize_commitment(c) for c in commitments],
        }

    # --- Relationship Intelligence Tools ---

    def serialize_interaction(interaction: Any) -> dict[str, Any]:
        """Serialize an interaction for MCP responses."""
        return {
            "id": str(interaction.id),
            "person_email": interaction.person_email,
            "person_name": interaction.person_name,
            "type": interaction.interaction_type.value,
            "direction": interaction.direction.value,
            "occurred_at": interaction.occurred_at.isoformat(),
            "subject": interaction.subject,
            "snippet": interaction.snippet,
            "source_type": interaction.source_type,
        }

    def serialize_relationship_summary(summary: Any) -> dict[str, Any]:
        """Serialize a relationship summary for MCP responses."""
        return {
            "person_email": summary.person_email,
            "person_name": summary.person_name,
            "importance": summary.importance,
            "last_contact_at": (
                summary.last_contact_at.isoformat() if summary.last_contact_at else None
            ),
            "last_contact_type": (
                summary.last_contact_type.value if summary.last_contact_type else None
            ),
            "last_contact_subject": summary.last_contact_subject,
            "days_since_contact": summary.days_since_contact,
            "total_interactions": summary.total_interactions,
            "inbound_count": summary.inbound_count,
            "outbound_count": summary.outbound_count,
            "open_commitments": summary.open_commitments,
            "open_follow_ups": summary.open_follow_ups,
            "is_stale": summary.is_stale,
            "needs_attention": summary.needs_attention,
        }

    @mcp.tool()
    async def get_relationship_summary(
        email: str,
        stale_threshold_days: int = 30,
    ) -> dict[str, Any]:
        """Get comprehensive relationship summary for a person.

        Shows:
        - Last contact date and type
        - Days since last contact
        - Total interaction count (inbound/outbound)
        - Open commitments and follow-ups
        - Whether the relationship is stale

        Use this before meetings to understand your relationship history.

        Args:
            email: Person's email address
            stale_threshold_days: Days without contact to consider stale (default: 30)

        Returns:
            Comprehensive relationship summary
        """
        from executive_copilot.admin.credentials import CredentialManager
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.relationship import RelationshipService

        cred_manager = CredentialManager(config.config_dir / ".credentials")
        db_manager = DatabaseManager(config.database)

        service = RelationshipService(
            config=config,
            credential_manager=cred_manager,
            db_manager=db_manager,
        )

        summary = await service.get_relationship_summary(
            email=email,
            stale_threshold_days=stale_threshold_days,
        )

        await db_manager.close()

        return serialize_relationship_summary(summary)

    @mcp.tool()
    async def get_person_history(
        email: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Get recent interaction history with a person.

        Shows the last N interactions (emails, meetings, etc.) with someone.
        Useful for preparing for meetings or refreshing your memory.

        Args:
            email: Person's email address
            limit: Maximum interactions to return (default: 5)

        Returns:
            Recent interactions with this person
        """
        from executive_copilot.admin.credentials import CredentialManager
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.relationship import RelationshipService

        cred_manager = CredentialManager(config.config_dir / ".credentials")
        db_manager = DatabaseManager(config.database)

        service = RelationshipService(
            config=config,
            credential_manager=cred_manager,
            db_manager=db_manager,
        )

        interactions = await service.get_person_history(email=email, limit=limit)

        await db_manager.close()

        return {
            "person_email": email.lower(),
            "total": len(interactions),
            "interactions": [serialize_interaction(i) for i in interactions],
        }

    @mcp.tool()
    async def get_stale_relationships(
        days_threshold: int = 30,
        min_importance: int = 5,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get important people you haven't contacted recently.

        Returns a list of relationships that may need attention based on:
        - No contact in the specified number of days
        - Person's importance level

        Use this to identify clients, partners, or colleagues you should reach out to.

        Args:
            days_threshold: Days without contact to be considered stale (default: 30)
            min_importance: Minimum importance level (1-10) to include (default: 5)
            limit: Maximum results (default: 20)

        Returns:
            List of stale relationships needing attention
        """
        from executive_copilot.admin.credentials import CredentialManager
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.relationship import RelationshipService

        cred_manager = CredentialManager(config.config_dir / ".credentials")
        db_manager = DatabaseManager(config.database)

        service = RelationshipService(
            config=config,
            credential_manager=cred_manager,
            db_manager=db_manager,
        )

        stale = await service.get_stale_relationships(
            days_threshold=days_threshold,
            min_importance=min_importance,
            limit=limit,
        )

        await db_manager.close()

        return {
            "days_threshold": days_threshold,
            "min_importance": min_importance,
            "total": len(stale),
            "relationships": [serialize_relationship_summary(s) for s in stale],
        }

    @mcp.tool()
    async def get_meeting_context(
        attendee_emails: list[str],
        history_limit: int = 5,
    ) -> dict[str, Any]:
        """Get relationship context for meeting attendees.

        For each attendee, shows:
        - Relationship summary (last contact, interaction count)
        - Recent interaction history
        - Open commitments and follow-ups

        Use this before meetings to be fully prepared.

        Args:
            attendee_emails: List of attendee email addresses
            history_limit: How many recent interactions to show per person (default: 5)

        Returns:
            Context for each attendee
        """
        from executive_copilot.admin.credentials import CredentialManager
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.relationship import RelationshipService

        cred_manager = CredentialManager(config.config_dir / ".credentials")
        db_manager = DatabaseManager(config.database)

        service = RelationshipService(
            config=config,
            credential_manager=cred_manager,
            db_manager=db_manager,
        )

        attendee_context: list[dict[str, Any]] = []
        for email in attendee_emails[:10]:
            summary = await service.get_relationship_summary(email)
            history = await service.get_person_history(email, limit=history_limit)

            attendee_context.append({
                "email": email.lower(),
                "summary": serialize_relationship_summary(summary),
                "recent_interactions": [serialize_interaction(i) for i in history],
            })

        await db_manager.close()

        needs_attention = [
            a for a in attendee_context
            if a["summary"]["needs_attention"]
        ]

        return {
            "total_attendees": len(attendee_context),
            "needs_attention_count": len(needs_attention),
            "attendees": attendee_context,
        }

    # --- Meeting Outcome Tools ---

    def serialize_meeting_outcome(outcome: Any) -> dict[str, Any]:
        """Serialize a meeting outcome for MCP responses."""
        return {
            "id": str(outcome.id),
            "event_id": outcome.event_id,
            "event_title": outcome.event_title,
            "meeting_date": outcome.meeting_date.isoformat(),
            "participants": outcome.participants,
            "organizer": outcome.organizer,
            "summary": outcome.summary,
            "decisions": outcome.decisions,
            "next_steps": outcome.next_steps,
            "key_points": outcome.key_points,
            "decision_count": outcome.decision_count,
            "next_step_count": outcome.next_step_count,
            "recorded_at": outcome.recorded_at.isoformat(),
            "tags": outcome.tags,
            "notes": outcome.notes,
        }

    def serialize_meeting_decision(decision: Any) -> dict[str, Any]:
        """Serialize a meeting decision for MCP responses."""
        return {
            "id": str(decision.id),
            "event_id": decision.event_id,
            "event_title": decision.event_title,
            "meeting_date": decision.meeting_date.isoformat(),
            "decision": decision.decision,
            "context": decision.context,
            "owner": decision.owner,
            "owner_email": decision.owner_email,
            "participants": decision.participants,
            "tags": decision.tags,
            "created_at": decision.created_at.isoformat(),
        }

    @mcp.tool()
    async def record_meeting_outcome(
        event_id: str,
        event_title: str,
        meeting_date: str,
        summary: str | None = None,
        decisions: list[str] | None = None,
        next_steps: list[str] | None = None,
        key_points: list[str] | None = None,
        participants: list[str] | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Record the outcomes of a meeting.

        Use this after meetings to capture:
        - What was decided
        - What's next (action items)
        - Key discussion points

        Args:
            event_id: Calendar event ID
            event_title: Meeting title
            meeting_date: Meeting date (YYYY-MM-DD or ISO format)
            summary: Brief meeting summary
            decisions: List of decisions made
            next_steps: List of action items/next steps
            key_points: Key discussion points
            participants: Attendee email addresses
            tags: Tags for categorization
            notes: Additional notes

        Returns:
            Recorded outcome details
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import (
            MeetingDecisionRepository,
            MeetingOutcomeRepository,
        )
        from executive_copilot.models.meeting import MeetingDecision, MeetingOutcome

        try:
            if "T" in meeting_date:
                parsed_date = datetime.fromisoformat(meeting_date.replace("Z", "+00:00"))
            else:
                parsed_date = datetime.combine(
                    datetime.fromisoformat(meeting_date).date(),
                    datetime.min.time(),
                    tzinfo=UTC,
                )
        except ValueError:
            return {"error": f"Invalid date format: {meeting_date}"}

        outcome = MeetingOutcome(
            id=uuid4(),
            created_at=datetime.now(UTC),
            updated_at=None,
            event_id=event_id,
            event_title=event_title,
            meeting_date=parsed_date,
            participants=participants or [],
            summary=summary,
            decisions=decisions or [],
            next_steps=next_steps or [],
            key_points=key_points or [],
            recorded_at=datetime.now(UTC),
            tags=tags or [],
            notes=notes,
        )

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            outcome_repo = MeetingOutcomeRepository(session)
            decision_repo = MeetingDecisionRepository(session)

            existing = await outcome_repo.get_by_event(event_id)
            if existing:
                outcome = outcome.with_update(id=existing.id, created_at=existing.created_at)
                await outcome_repo.update(outcome)
            else:
                outcome = await outcome_repo.create(outcome)

            for decision_text in (decisions or []):
                decision = MeetingDecision(
                    id=uuid4(),
                    created_at=datetime.now(UTC),
                    updated_at=None,
                    event_id=event_id,
                    event_title=event_title,
                    meeting_date=parsed_date,
                    decision=decision_text,
                    participants=participants or [],
                    tags=tags or [],
                )
                await decision_repo.create(decision)

            await session.commit()

        await db_manager.close()

        return {
            "success": True,
            "outcome": serialize_meeting_outcome(outcome),
            "decisions_recorded": len(decisions or []),
        }

    @mcp.tool()
    async def get_meeting_outcome(event_id: str) -> dict[str, Any]:
        """Get the recorded outcome for a specific meeting.

        Args:
            event_id: Calendar event ID

        Returns:
            Meeting outcome if recorded
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import MeetingOutcomeRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = MeetingOutcomeRepository(session)
            outcome = await repo.get_by_event(event_id)

        await db_manager.close()

        if not outcome:
            return {"error": f"No outcome recorded for event {event_id}"}

        return serialize_meeting_outcome(outcome)

    @mcp.tool()
    async def get_recent_meeting_outcomes(
        days_back: int = 30,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get recently recorded meeting outcomes.

        Args:
            days_back: How far back to look (default: 30 days)
            limit: Maximum results (default: 20)

        Returns:
            Recent meeting outcomes
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import MeetingOutcomeRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = MeetingOutcomeRepository(session)
            outcomes = await repo.get_recent(days_back=days_back, limit=limit)

        await db_manager.close()

        return {
            "days_back": days_back,
            "total": len(outcomes),
            "outcomes": [serialize_meeting_outcome(o) for o in outcomes],
        }

    @mcp.tool()
    async def search_decisions(
        query: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the decision log.

        Search for decisions made in meetings by text.

        Args:
            query: Search term
            limit: Maximum results (default: 20)

        Returns:
            Matching decisions with meeting context
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import MeetingDecisionRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = MeetingDecisionRepository(session)
            decisions = await repo.search(query, limit=limit)

        await db_manager.close()

        return {
            "query": query,
            "total": len(decisions),
            "decisions": [serialize_meeting_decision(d) for d in decisions],
        }

    @mcp.tool()
    async def get_recent_decisions(
        days_back: int = 30,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Get recent decisions from all meetings.

        Args:
            days_back: How far back to look (default: 30 days)
            limit: Maximum results (default: 30)

        Returns:
            Recent decisions with meeting context
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import MeetingDecisionRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = MeetingDecisionRepository(session)
            decisions = await repo.get_recent(days_back=days_back, limit=limit)

        await db_manager.close()

        return {
            "days_back": days_back,
            "total": len(decisions),
            "decisions": [serialize_meeting_decision(d) for d in decisions],
        }

    @mcp.tool()
    async def get_meeting_decisions(event_id: str) -> dict[str, Any]:
        """Get all decisions from a specific meeting.

        Args:
            event_id: Calendar event ID

        Returns:
            Decisions made in that meeting
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import MeetingDecisionRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = MeetingDecisionRepository(session)
            decisions = await repo.get_by_event(event_id)

        await db_manager.close()

        return {
            "event_id": event_id,
            "total": len(decisions),
            "decisions": [serialize_meeting_decision(d) for d in decisions],
        }

    # --- Delegation Tools ---

    def serialize_delegation(delegation: Any) -> dict[str, Any]:
        """Serialize a delegation domain model for MCP responses."""
        return {
            "id": str(delegation.id),
            "title": delegation.title,
            "description": delegation.description,
            "original_text": delegation.original_text,
            "assignee_name": delegation.assignee_name,
            "assignee_email": delegation.assignee_email,
            "status": delegation.status.value,
            "priority": delegation.priority.value,
            "delegated_at": delegation.delegated_at.isoformat(),
            "due_date": delegation.due_date.isoformat() if delegation.due_date else None,
            "completed_at": delegation.completed_at.isoformat() if delegation.completed_at else None,
            "source_type": delegation.source_type,
            "auto_detected": delegation.auto_detected,
            "is_overdue": delegation.is_overdue,
            "days_waiting": delegation.days_waiting,
            "days_until_due": delegation.days_until_due,
            "tags": delegation.tags,
            "notes": delegation.notes,
            "created_at": delegation.created_at.isoformat(),
        }

    def serialize_delegation_summary(summary: Any) -> dict[str, Any]:
        """Serialize a delegation summary for MCP responses."""
        return {
            "assignee_email": summary.assignee_email,
            "assignee_name": summary.assignee_name,
            "pending_count": summary.pending_count,
            "in_progress_count": summary.in_progress_count,
            "overdue_count": summary.overdue_count,
            "oldest_delegation_days": summary.oldest_delegation_days,
            "total_waiting": summary.total_waiting,
        }

    @mcp.tool()
    async def get_delegations(
        include_completed: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get tasks you've delegated to others.

        Shows things you've asked others to do, such as:
        - "Can you handle the client report?"
        - "Please review the contract by Friday"
        - "Could you coordinate with the team?"

        Args:
            include_completed: Include completed delegations (default: False)
            limit: Maximum results (default: 50)

        Returns:
            Delegations with status summary
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import DelegationRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = DelegationRepository(session)
            delegations = await repo.get_pending(
                include_in_progress=True,
                limit=limit,
            )

            if include_completed:
                from sqlalchemy import select

                from executive_copilot.database.tables import DelegationTable
                from executive_copilot.models.delegation import DelegationStatus

                stmt = (
                    select(DelegationTable)
                    .where(DelegationTable.status == DelegationStatus.COMPLETED.value)  # type: ignore[arg-type]
                    .order_by(DelegationTable.completed_at.desc())  # type: ignore[union-attr]
                    .limit(limit)
                )
                result = await session.execute(stmt)
                completed_rows = result.scalars().all()
                completed = [repo._to_model(row) for row in completed_rows]
                delegations = delegations + completed

        await db_manager.close()

        overdue = [d for d in delegations if d.is_overdue]
        pending = [d for d in delegations if d.status.value == "pending" and not d.is_overdue]
        in_progress = [d for d in delegations if d.status.value == "in_progress"]

        return {
            "total": len(delegations),
            "summary": {
                "overdue": len(overdue),
                "pending": len(pending),
                "in_progress": len(in_progress),
            },
            "delegations": [serialize_delegation(d) for d in delegations],
        }

    @mcp.tool()
    async def get_waiting_summary() -> dict[str, Any]:
        """Get a summary of what you're waiting on from each person.

        Shows counts like "Waiting on: 3 items from Sarah, 2 from Mike".
        Groups pending delegations by assignee.

        Returns:
            Summary per person with counts and oldest item age
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import DelegationRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = DelegationRepository(session)
            summaries = await repo.get_waiting_summary()

        await db_manager.close()

        total_waiting = sum(s.total_waiting for s in summaries)
        total_overdue = sum(s.overdue_count for s in summaries)

        formatted = []
        for s in summaries:
            name = s.assignee_name or s.assignee_email.split("@")[0]
            formatted.append(f"{s.total_waiting} from {name}")

        return {
            "total_waiting": total_waiting,
            "total_overdue": total_overdue,
            "summary_text": f"Waiting on: {', '.join(formatted[:5])}" if formatted else "No pending delegations",
            "by_person": [serialize_delegation_summary(s) for s in summaries],
        }

    @mcp.tool()
    async def get_delegations_to_person(
        email: str,
        include_completed: bool = False,
    ) -> dict[str, Any]:
        """Get delegations assigned to a specific person.

        Args:
            email: Assignee's email address
            include_completed: Include completed delegations

        Returns:
            Delegations to this person
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import DelegationRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = DelegationRepository(session)
            delegations = await repo.get_by_assignee(
                email=email,
                include_completed=include_completed,
            )

        await db_manager.close()

        overdue = [d for d in delegations if d.is_overdue]

        return {
            "assignee_email": email.lower(),
            "total": len(delegations),
            "overdue_count": len(overdue),
            "delegations": [serialize_delegation(d) for d in delegations],
        }

    @mcp.tool()
    async def get_overdue_delegations() -> dict[str, Any]:
        """Get all overdue delegations.

        Returns delegations past their due date that haven't been completed.

        Returns:
            Overdue delegations ordered by due date
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import DelegationRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = DelegationRepository(session)
            delegations = await repo.get_overdue()

        await db_manager.close()

        return {
            "total": len(delegations),
            "delegations": [serialize_delegation(d) for d in delegations],
        }

    @mcp.tool()
    async def create_delegation(
        title: str,
        assignee_email: str,
        description: str | None = None,
        due_date: str | None = None,
        priority: str = "medium",
        assignee_name: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new delegation manually.

        Use this to track a task you've assigned to someone.

        Args:
            title: Brief description of the task
            assignee_email: Email of person assigned
            description: Detailed notes
            due_date: When the task is due (YYYY-MM-DD format)
            priority: Priority level (low, medium, high, urgent)
            assignee_name: Name of person assigned
            tags: Tags for categorization

        Returns:
            Created delegation details
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import DelegationRepository
        from executive_copilot.models.delegation import (
            Delegation,
            DelegationPriority,
            DelegationStatus,
        )

        parsed_due = None
        if due_date:
            parsed_due = datetime.combine(
                datetime.fromisoformat(due_date).date(),
                datetime.min.time(),
                tzinfo=UTC,
            )

        try:
            priority_enum = DelegationPriority(priority)
        except ValueError:
            return {"error": f"Invalid priority: {priority}. Use low, medium, high, or urgent."}

        delegation = Delegation(
            id=uuid4(),
            created_at=datetime.now(UTC),
            updated_at=None,
            title=title,
            description=description,
            assignee_email=assignee_email.lower(),
            assignee_name=assignee_name,
            status=DelegationStatus.PENDING,
            priority=priority_enum,
            delegated_at=datetime.now(UTC),
            due_date=parsed_due,
            auto_detected=False,
            tags=tags or [],
        )

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = DelegationRepository(session)
            created = await repo.create(delegation)
            await session.commit()

        await db_manager.close()

        return {
            "success": True,
            "delegation": serialize_delegation(created),
        }

    @mcp.tool()
    async def complete_delegation(delegation_id: str) -> dict[str, Any]:
        """Mark a delegation as completed.

        Args:
            delegation_id: ID of the delegation to complete

        Returns:
            Updated delegation details
        """
        from uuid import UUID

        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import DelegationRepository

        try:
            del_uuid = UUID(delegation_id)
        except ValueError:
            return {"error": f"Invalid delegation ID: {delegation_id}"}

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = DelegationRepository(session)
            delegation = await repo.get_by_id(del_uuid)

            if not delegation:
                await db_manager.close()
                return {"error": f"Delegation not found: {delegation_id}"}

            updated = delegation.mark_completed()
            await repo.update(updated)
            await session.commit()

        await db_manager.close()

        return {
            "success": True,
            "delegation": serialize_delegation(updated),
        }

    @mcp.tool()
    async def update_delegation(
        delegation_id: str,
        status: str | None = None,
        priority: str | None = None,
        due_date: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Update a delegation.

        Args:
            delegation_id: ID of the delegation to update
            status: New status (pending, in_progress, completed, cancelled)
            priority: New priority (low, medium, high, urgent)
            due_date: New due date (YYYY-MM-DD format)
            notes: Additional notes to add

        Returns:
            Updated delegation details
        """
        from uuid import UUID

        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import DelegationRepository
        from executive_copilot.models.delegation import DelegationPriority, DelegationStatus

        try:
            del_uuid = UUID(delegation_id)
        except ValueError:
            return {"error": f"Invalid delegation ID: {delegation_id}"}

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = DelegationRepository(session)
            delegation = await repo.get_by_id(del_uuid)

            if not delegation:
                await db_manager.close()
                return {"error": f"Delegation not found: {delegation_id}"}

            updates: dict[str, Any] = {}

            if status:
                try:
                    new_status = DelegationStatus(status)
                    updates["status"] = new_status
                    if new_status == DelegationStatus.COMPLETED:
                        updates["completed_at"] = datetime.utcnow()
                except ValueError:
                    await db_manager.close()
                    return {"error": f"Invalid status: {status}"}

            if priority:
                try:
                    updates["priority"] = DelegationPriority(priority)
                except ValueError:
                    await db_manager.close()
                    return {"error": f"Invalid priority: {priority}"}

            if due_date:
                updates["due_date"] = datetime.combine(
                    datetime.fromisoformat(due_date).date(),
                    datetime.min.time(),
                    tzinfo=UTC,
                )

            if notes:
                existing_notes = delegation.notes or ""
                timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                updates["notes"] = f"{existing_notes}\n[{timestamp}] {notes}".strip()

            if updates:
                updates["updated_at"] = datetime.utcnow()
                updated = delegation.with_update(**updates)
                await repo.update(updated)
                await session.commit()
                delegation = updated

        await db_manager.close()

        return {
            "success": True,
            "delegation": serialize_delegation(delegation),
        }

    @mcp.tool()
    async def search_delegations(query: str, limit: int = 20) -> dict[str, Any]:
        """Search delegations by title or description.

        Args:
            query: Search term
            limit: Maximum results

        Returns:
            Matching delegations
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.database.repositories import DelegationRepository

        db_manager = DatabaseManager(config.database)
        async with db_manager.session() as session:
            repo = DelegationRepository(session)
            delegations = await repo.search(query, limit=limit)

        await db_manager.close()

        return {
            "query": query,
            "total": len(delegations),
            "delegations": [serialize_delegation(d) for d in delegations],
        }

    # --- Organization Tools ---

    @mcp.tool()
    async def list_organizations() -> dict[str, Any]:
        """List all configured organizations with their accounts.

        Returns:
            List of organizations with associated accounts and authorization status
        """
        orgs = []
        for org in config.organizations:
            accounts = []
            for pa in org.provider_accounts:
                authorized = is_account_authorized(pa.provider_type, pa.provider_name, pa.account_id)
                accounts.append({
                    "type": pa.provider_type,
                    "provider": pa.provider_name,
                    "account": pa.account_id,
                    "authorized": authorized,
                })

            orgs.append({
                "name": org.name,
                "short_name": org.short_name,
                "type": org.organization_type,
                "domains": org.domains,
                "importance": org.importance,
                "accounts": accounts,
            })

        return {"count": len(orgs), "organizations": orgs}

    @mcp.tool()
    async def list_accounts() -> dict[str, Any]:
        """List all configured accounts with authorization status.

        Returns:
            All email and calendar accounts with their status
        """
        email_accounts = get_email_accounts()
        calendar_accounts = get_calendar_accounts()
        task_accounts = get_task_accounts()

        result: dict[str, Any] = {
            "email_accounts": [],
            "calendar_accounts": [],
            "task_accounts": [],
        }

        for acc in email_accounts:
            authorized = is_account_authorized("email", acc["provider"], acc["account_id"])
            result["email_accounts"].append({
                "account": acc["account_id"],
                "provider": acc["provider"],
                "organization": acc["organization"],
                "authorized": authorized,
            })

        for acc in calendar_accounts:
            authorized = is_account_authorized("calendar", acc["provider"], acc["account_id"])
            result["calendar_accounts"].append({
                "account": acc["account_id"],
                "provider": acc["provider"],
                "organization": acc["organization"],
                "authorized": authorized,
            })

        for acc in task_accounts:
            authorized = is_account_authorized("task", acc["provider"], acc["account_id"])
            result["task_accounts"].append({
                "account": acc["account_id"],
                "provider": acc["provider"],
                "organization": acc["organization"],
                "authorized": authorized,
            })

        return result

    @mcp.tool()
    async def get_organization_emails(
        organization: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get recent emails related to a specific organization.

        Args:
            organization: Organization name (e.g., "Primary", "Secondary")
            limit: Maximum results

        Returns:
            Recent emails from/to the organization's domains
        """
        org = None
        for o in config.organizations:
            if o.name.lower() == organization.lower():
                org = o
                break

        if not org:
            return {"error": f"Organization '{organization}' not found"}

        email_accounts = [
            pa for pa in org.provider_accounts
            if pa.provider_type == "email"
        ]

        if not email_accounts:
            return {"error": f"No email accounts configured for {organization}"}

        results: dict[str, Any] = {
            "organization": org.name,
            "domains": org.domains,
            "emails": [],
            "errors": [],
        }

        async def fetch_org_emails(pa: Any) -> tuple[str, list[Any], str | None]:
            if not is_account_authorized("email", pa.provider_name, pa.account_id):
                return pa.account_id, [], "Not authorized"
            try:
                provider = get_email_provider(pa.provider_name, pa.account_id)
                all_emails = []
                for domain in org.domains[:3]:
                    emails = await provider.search(f"from:{domain} OR to:{domain}", max_results=limit)
                    all_emails.extend(emails)
                return pa.account_id, all_emails, None
            except Exception as e:
                return pa.account_id, [], str(e)

        tasks = [fetch_org_emails(pa) for pa in email_accounts]
        responses = await asyncio.gather(*tasks)

        seen = set()
        for account_id, emails, error in responses:
            if error:
                results["errors"].append({"account": account_id, "error": error})
            else:
                for e in emails:
                    key = (e.subject, e.sender.address, e.received_at.isoformat())
                    if key not in seen:
                        seen.add(key)
                        results["emails"].append({
                            "account": account_id,
                            "subject": e.subject,
                            "from": e.sender.address,
                            "from_name": e.sender.display_name,
                            "received_at": e.received_at.isoformat(),
                            "snippet": e.snippet,
                        })

        results["emails"].sort(key=lambda x: x["received_at"], reverse=True)
        results["emails"] = results["emails"][:limit]
        results["count"] = len(results["emails"])
        return results

    # --- Meeting Preparation ---

    @mcp.tool()
    async def get_event_details(
        event_id: str,
        account: str,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        """Get detailed information about a specific calendar event.

        Args:
            event_id: The event ID from the calendar provider
            account: Calendar account that contains this event
            calendar_id: Optional calendar ID (defaults to primary)

        Returns:
            Event details including attendees, location, and conference info
        """
        accounts = get_calendar_accounts()
        acc = next((a for a in accounts if a["account_id"] == account), None)
        if not acc:
            return {"error": f"Account {account} not found"}

        if not is_account_authorized("calendar", acc["provider"], acc["account_id"]):
            return {"error": f"Account {account} is not authorized"}

        try:
            provider = get_calendar_provider(acc["provider"], acc["account_id"])
            event = await provider.get_event(event_id, calendar_id)

            if not event:
                return {"error": f"Event {event_id} not found"}

            return {
                "event_id": event_id,
                "account": account,
                "title": event.title,
                "description": event.description,
                "start": event.start_time.isoformat(),
                "end": event.end_time.isoformat(),
                "duration_minutes": event.duration_minutes,
                "is_all_day": event.is_all_day,
                "location": event.location.display_name if event.location else None,
                "conference_url": event.conference_url,
                "organizer": {
                    "email": event.organizer.email,
                    "name": event.organizer.display_name,
                } if event.organizer else None,
                "attendees": [
                    {
                        "email": a.email,
                        "name": a.display_name,
                        "response": a.response_status.value,
                        "is_optional": a.is_optional,
                        "is_organizer": a.is_organizer,
                    }
                    for a in event.attendees
                ],
                "is_recurring": event.is_recurring,
                "status": event.status.value,
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def prepare_for_meeting(
        event_id: str,
        account: str,
        calendar_id: str | None = None,
        include_ai_summary: bool = True,
    ) -> dict[str, Any]:
        """Prepare comprehensive context for an upcoming meeting.

        Gathers event details, attendee information, recent emails with attendees,
        related tasks, and optionally generates AI-powered talking points.

        Args:
            event_id: The event ID from the calendar provider
            account: Calendar account that contains this event
            calendar_id: Optional calendar ID (defaults to primary)
            include_ai_summary: Generate AI talking points (requires OpenAI API key)

        Returns:
            Comprehensive meeting preparation including:
            - Event details and attendees
            - Recent email threads with attendees
            - Related tasks
            - Organization context
            - AI-generated talking points and agenda suggestions
        """
        # Get event details
        event_result = await get_event_details(event_id, account, calendar_id)
        if "error" in event_result:
            return event_result

        prep: dict[str, Any] = {
            "event": event_result,
            "attendees_context": [],
            "organization": None,
            "related_emails": [],
            "related_tasks": [],
            "ai_summary": None,
            "errors": [],
        }

        attendee_emails = [a["email"] for a in event_result.get("attendees", [])]
        external_attendees = [
            e for e in attendee_emails
            if not any(e.endswith(f"@{d}") for org in config.organizations for d in org.domains)
        ]

        # Detect organization from attendee domains
        detected_org = None
        for attendee_email in external_attendees:
            domain = attendee_email.split("@")[-1] if "@" in attendee_email else None
            if domain:
                for org in config.organizations:
                    if domain in org.domains:
                        detected_org = org
                        break
            if detected_org:
                break

        if detected_org:
            prep["organization"] = {
                "name": detected_org.name,
                "short_name": detected_org.short_name,
                "type": detected_org.organization_type,
                "importance": detected_org.importance,
                "domains": detected_org.domains,
                "tags": detected_org.tags,
            }

        # Gather context for each external attendee
        email_accounts = get_email_accounts()
        for attendee_email in external_attendees[:5]:  # Limit to 5 attendees
            attendee_context: dict[str, Any] = {
                "email": attendee_email,
                "name": next(
                    (a["name"] for a in event_result.get("attendees", [])
                     if a["email"] == attendee_email),
                    None
                ),
                "recent_emails": [],
            }

            # Search for recent emails with this attendee
            for acc in email_accounts:
                if not is_account_authorized("email", acc["provider"], acc["account_id"]):
                    continue
                try:
                    provider = get_email_provider(acc["provider"], acc["account_id"])
                    emails = await provider.search(
                        f"from:{attendee_email} OR to:{attendee_email}",
                        max_results=5,
                    )
                    for e in emails:
                        attendee_context["recent_emails"].append({
                            "subject": e.subject,
                            "from": e.sender.address,
                            "date": e.received_at.isoformat(),
                            "snippet": e.snippet,
                        })
                except Exception as ex:
                    prep["errors"].append(f"Email search failed for {attendee_email}: {ex}")

            prep["attendees_context"].append(attendee_context)

        # Search for emails mentioning meeting subject
        meeting_subject = event_result.get("title", "")
        if meeting_subject and len(meeting_subject) > 3:
            subject_keywords = " ".join(meeting_subject.split()[:4])
            for acc in email_accounts[:2]:  # Limit accounts searched
                if not is_account_authorized("email", acc["provider"], acc["account_id"]):
                    continue
                try:
                    provider = get_email_provider(acc["provider"], acc["account_id"])
                    emails = await provider.search(subject_keywords, max_results=5)
                    for e in emails:
                        prep["related_emails"].append({
                            "subject": e.subject,
                            "from": e.sender.address,
                            "date": e.received_at.isoformat(),
                            "snippet": e.snippet,
                            "account": acc["account_id"],
                        })
                except Exception as ex:
                    prep["errors"].append(f"Subject search failed: {ex}")

        # Get related tasks
        task_accounts = get_task_accounts()
        for acc in task_accounts:
            if not is_account_authorized("task", acc["provider"], acc["account_id"]):
                continue
            try:
                task_provider = get_task_provider(acc["provider"], acc["account_id"])
                tasks = await task_provider.get_my_tasks(include_completed=False)
                for task in tasks[:20]:
                    task_title = task.title.lower() if task.title else ""
                    meeting_title = meeting_subject.lower()
                    # Check if task is related to meeting
                    if (any(word in task_title for word in meeting_title.split() if len(word) > 3)
                        or (detected_org and detected_org.name.lower() in task_title)):
                        prep["related_tasks"].append({
                            "title": task.title,
                            "status": task.status.value,
                            "due_date": task.due_date.isoformat() if task.due_date else None,
                            "project": task.project_name,
                            "url": task.provider_ref.external_url if task.provider_ref else None,
                        })
            except Exception as ex:
                prep["errors"].append(f"Task search failed: {ex}")

        # Generate AI summary if requested
        if include_ai_summary:
            try:
                ai_summary = await _generate_meeting_prep_summary(
                    config, credential_manager, prep
                )
                prep["ai_summary"] = ai_summary
            except Exception as ex:
                prep["errors"].append(f"AI summary generation failed: {ex}")
                prep["ai_summary"] = None

        return prep

    async def _generate_meeting_prep_summary(
        config_obj: AppConfig,
        cred_manager: CredentialManager,
        prep_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Generate AI-powered meeting preparation summary."""
        from executive_copilot.database import DatabaseManager
        from executive_copilot.providers.base import ProviderRegistry
        from executive_copilot.services.ai import AIService, AITextRequest
        from executive_copilot.services.container import ServiceContainer

        # Build context for AI
        event = prep_data.get("event", {})
        org = prep_data.get("organization")
        attendees = prep_data.get("attendees_context", [])
        related_emails = prep_data.get("related_emails", [])
        related_tasks = prep_data.get("related_tasks", [])

        context_parts = [
            f"Meeting: {event.get('title', 'Unknown')}",
            f"Time: {event.get('start', '')} to {event.get('end', '')}",
            f"Duration: {event.get('duration_minutes', 0)} minutes",
        ]

        if event.get("description"):
            context_parts.append(f"Description: {event['description'][:500]}")

        if org:
            context_parts.append(f"Organization: {org['name']} ({org['type']}, importance: {org['importance']}/10)")

        if attendees:
            attendee_info = []
            for att in attendees[:5]:
                att_str = att["email"]
                if att.get("name"):
                    att_str = f"{att['name']} <{att['email']}>"
                if att.get("recent_emails"):
                    att_str += f" - {len(att['recent_emails'])} recent emails"
                attendee_info.append(att_str)
            context_parts.append(f"Attendees: {', '.join(attendee_info)}")

        if related_emails:
            email_summaries = [
                f"- {e['subject']} (from {e['from']}, {e['date'][:10]})"
                for e in related_emails[:5]
            ]
            context_parts.append("Recent related emails:\n" + "\n".join(email_summaries))

        if related_tasks:
            task_summaries = [
                f"- {t['title']} ({t['status']}, due: {t.get('due_date', 'no date')[:10] if t.get('due_date') else 'no date'})"
                for t in related_tasks[:5]
            ]
            context_parts.append("Related tasks:\n" + "\n".join(task_summaries))

        context_text = "\n\n".join(context_parts)

        prompt = f"""Based on the following meeting context, provide a brief preparation summary:

{context_text}

Please provide:
1. **Key Talking Points** (3-5 bullet points of what to discuss or bring up)
2. **Preparation Actions** (1-3 things to do before the meeting)
3. **Context Summary** (2-3 sentences summarizing relevant background)

Keep it concise and actionable. Focus on what's most important for this specific meeting."""

        try:
            # Create minimal service container for AI
            db_manager = DatabaseManager(config_obj.database)
            registry = ProviderRegistry()
            container = ServiceContainer(config_obj, db_manager, registry)

            ai_service = AIService(container)
            result = await ai_service.generate_text(
                AITextRequest(
                    input=prompt,
                    instructions="You are an executive assistant helping prepare for meetings. Be concise, professional, and actionable.",
                    max_output_tokens=800,
                    temperature=0.7,
                )
            )

            await db_manager.close()

            return {
                "summary": result.text,
                "model": result.model,
            }
        except Exception as e:
            logger.warning("AI summary generation failed", error=str(e))
            return None

    # --- Morning Briefing ---

    @mcp.tool()
    async def get_morning_briefing() -> dict[str, Any]:
        """Get a comprehensive morning briefing.

        Aggregates:
        - Today's calendar events
        - Unread and urgent emails
        - Overdue items (follow-ups, commitments, delegations)
        - Pending replies awaiting response
        - What you're waiting on from others

        Returns a summary like "3 meetings, 2 pending replies, 1 overdue item"
        along with full details for each category.

        Returns:
            Complete briefing with summary and detailed sections
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.briefing import BriefingService

        db_manager = DatabaseManager(config.database)

        service = BriefingService(
            config=config,
            credential_manager=credential_manager,
            db_manager=db_manager,
        )

        briefing = await service.generate_briefing()
        await db_manager.close()

        return briefing

    @mcp.tool()
    async def get_daily_briefing() -> dict[str, Any]:
        """Get a quick daily briefing summary.

        Lighter-weight version that just shows email and calendar counts.
        Use get_morning_briefing for the full comprehensive briefing.

        Returns:
            Summary of unread emails and today's events across all accounts
        """
        email_summary = await get_email_summary()
        today_events = await get_today_events()

        return {
            "date": datetime.now(UTC).date().isoformat(),
            "email": {
                "total_unread": email_summary["total_unread"],
                "by_organization": email_summary["by_organization"],
            },
            "calendar": {
                "total_events": today_events["total_events"],
                "by_organization": today_events["by_organization"],
                "events": today_events["events"],
            },
            "accounts_status": email_summary["accounts"],
        }

    @mcp.tool()
    async def send_briefing_email(
        to_email: str | None = None,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Send the morning briefing as an email digest.

        Generates a comprehensive briefing and sends it as a formatted email.
        Useful for receiving your daily summary in your inbox.

        Args:
            to_email: Recipient email (defaults to your email account)
            account: Email account to send from (uses first available if not specified)

        Returns:
            Send result with success status
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.briefing import BriefingService

        db_manager = DatabaseManager(config.database)

        service = BriefingService(
            config=config,
            credential_manager=credential_manager,
            db_manager=db_manager,
        )

        briefing = await service.generate_briefing()
        result = await service.send_briefing_email(
            briefing=briefing,
            to_email=to_email,
            account_id=account,
        )

        await db_manager.close()
        return result

    @mcp.tool()
    async def get_briefing_summary() -> dict[str, Any]:
        """Get a quick one-line summary of your day.

        Returns a summary like "3 meetings, 2 pending replies, 1 overdue task"
        without the full details - perfect for a quick status check.

        Returns:
            Summary text and counts
        """
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.briefing import BriefingService

        db_manager = DatabaseManager(config.database)

        service = BriefingService(
            config=config,
            credential_manager=credential_manager,
            db_manager=db_manager,
        )

        briefing = await service.generate_briefing()
        await db_manager.close()

        return {
            "summary_text": briefing.get("summary_text"),
            "counts": briefing.get("summary"),
            "date": briefing.get("date"),
        }

    logger.info("MCP server configured with multi-account aggregation tools")
    return mcp


def main() -> None:
    """Run the MCP server."""
    import logging
    import os
    import sys

    # MCP uses stdio for communication, so ALL logging must go to stderr
    # Set this BEFORE any imports that might log
    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
        level=logging.ERROR,  # Only errors - suppress everything else
    )

    # Suppress all loggers
    for name in ["executive_copilot", "httpx", "httpcore", "structlog", "root", ""]:
        logging.getLogger(name).setLevel(logging.ERROR)

    # Suppress structlog output
    os.environ["STRUCTLOG_LEVEL"] = "ERROR"

    from executive_copilot.config import load_config

    config = load_config()
    mcp = create_mcp_server(config)
    mcp.run()


if __name__ == "__main__":
    main()
