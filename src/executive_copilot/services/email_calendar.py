"""Find e-mails that need a calendar entry and add them to the default calendar.

Flow (review first, never auto-create):

1. ``review`` lists recent Outlook mail, drops ignored senders and items
   already handled, then
2. triages subjects/previews in ONE AI call to find mail that implies a
   calendar entry (appointments, school events, invitations, reservations,
   flights, dated deadlines) and ignores marketing/newsletters/receipts,
3. extracts date/time/location from each candidate's full body (Outlook
   meeting requests are read deterministically from Graph), and
4. flags proposals that already exist on the default calendar.

``add_to_calendar`` creates the event on the default calendar (the Paycode
Google calendar unless configured otherwise) and remembers the e-mail so it is
not proposed again; ``dismiss`` remembers it without creating anything.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from executive_copilot.logging import get_logger
from executive_copilot.services.obsidian_notes import html_to_text, normalize, parse_ai_json

logger = get_logger(__name__)

ITEM_TYPE = "email_calendar"
JOB_NAME = "email_calendar"

TRIAGE_PROMPT = """You help an executive keep their calendar complete.
Today is {today} ({tz}). Below are recent e-mails (index | from | subject | preview).

Return ONLY a JSON array with the indexes of e-mails that require the
executive to block time or attend/act on a specific future date:
appointments, meeting or event invitations from real people/organizations
they deal with, school/family events, reservations, flights or travel,
deadlines or payments due on a concrete date stated in the e-mail.

Be strict; when in doubt, leave it out. Always exclude: marketing and
promotions, paid surveys or user-research solicitations, newsletters,
receipts and refunds, order/shipping/cancellation updates, subscription
renewals or billing notices, account/security alerts, statements without a
due date, and anything already in the past.

E-mails:
{emails}
"""

EXTRACT_PROMPT = """Extract the calendar entry implied by this e-mail.
Today is {today}. Timezone: {tz}. The e-mail was received {received}.
Resolve relative dates ("mañana", "next Friday") against the received date.

Return ONLY a JSON object:
{{
  "needs_calendar": true/false,
  "title": "short event title in the e-mail's language",
  "start": "YYYY-MM-DDTHH:MM" (local time) or "YYYY-MM-DD" when no time is given,
  "end": "YYYY-MM-DDTHH:MM" or "YYYY-MM-DD" or null,
  "location": "place or meeting link" or null,
  "notes": "1-2 lines the executive needs (what to bring, who, cost)" or null,
  "confidence": 0.0-1.0,
  "reason": "why this belongs on the calendar"
}}

Rules:
- needs_calendar is false for marketing, paid surveys, refunds, order or
  subscription notices, and when the e-mail states no concrete date.
- Never invent a time: if the e-mail gives only a date, use "YYYY-MM-DD".
- confidence reflects how sure you are that the executive must be there or
  act on that date.

From: {sender}
Subject: {subject}

{body}
"""

KEYWORDS = re.compile(
    r"(?i)\b(invitaci[oó]n|invitation|invite|cita|appointment|reuni[oó]n|meeting|junta|"
    r"evento|event|webinar|reservaci[oó]n|reservation|booking|vuelo|flight|itinerar|"
    r"recordatorio|reminder|save the date|rsvp|fecha l[ií]mite|deadline|vence|"
    r"visit|visita|festival|ceremon|graduaci[oó]n|kermes|excursi[oó]n|field trip)"
)


def parse_local(value: str, tz: ZoneInfo) -> tuple[datetime, bool]:
    """Parse 'YYYY-MM-DD' (all-day) or 'YYYY-MM-DDTHH:MM[:SS]' in local time.

    Returns:
        (aware datetime, is_all_day)
    """
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(date.fromisoformat(value), time.min, tzinfo=tz), True
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed, False


def titles_match(a: str, b: str) -> bool:
    """Loose title similarity used for duplicate detection."""
    ta, tb = set(normalize(a).split()), set(normalize(b).split())
    ta = {t for t in ta if len(t) > 2}
    tb = {t for t in tb if len(t) > 2}
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.5


def graph_event_fields(raw: dict[str, Any], tz: ZoneInfo) -> dict[str, Any] | None:
    """Proposal fields from an Outlook meeting request (eventMessage)."""
    if "eventMessage" not in (raw.get("@odata.type") or ""):
        return None
    start = raw.get("startDateTime") or {}
    end = raw.get("endDateTime") or {}
    if not start.get("dateTime"):
        return None

    def to_local(dt: dict[str, Any]) -> str:
        source_tz = dt.get("timeZone") or "UTC"
        try:
            zone = ZoneInfo(source_tz)
        except Exception:
            zone = ZoneInfo("UTC")
        parsed = datetime.fromisoformat(dt["dateTime"][:19]).replace(tzinfo=zone)
        return parsed.astimezone(tz).strftime("%Y-%m-%dT%H:%M")

    all_day = bool(raw.get("isAllDay"))
    s = to_local(start)
    e = to_local(end) if end.get("dateTime") else None
    return {
        "needs_calendar": raw.get("meetingMessageType") not in ("meetingCancelled",),
        "title": raw.get("subject") or "Reunión",
        "start": s[:10] if all_day else s,
        "end": (e[:10] if all_day else e) if e else None,
        "location": (raw.get("location") or {}).get("displayName") or None,
        "notes": None,
        "confidence": 0.95,
        "reason": f"Outlook meeting request ({raw.get('meetingMessageType') or 'invite'})",
    }


class EmailCalendarService:
    """Review mail for calendar-worthy items and create events on the default calendar."""

    def __init__(
        self,
        config: Any,
        credential_manager: Any,
        db_manager: Any | None = None,
        ai_service: Any | None = None,
    ) -> None:
        self.config = config
        self.credential_manager = credential_manager
        self.db_manager = db_manager
        self._ai = ai_service
        self.defaults = config.calendar_defaults
        self.settings = config.email_calendar
        self.tz = ZoneInfo(self.defaults.timezone)

    # --- accounts ---------------------------------------------------------

    def source_accounts(self) -> list[str]:
        if self.settings.source_accounts:
            return list(self.settings.source_accounts)
        seen: list[str] = []
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "email" and pa.provider_name == "outlook" and pa.account_id not in seen:
                    seen.append(pa.account_id)
        return seen

    def _org_type_for(self, account_id: str) -> str | None:
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                if pa.account_id == account_id:
                    return str(org.organization_type)
        return None

    def default_calendar(self) -> tuple[str, str, str]:
        """(provider, account_id, calendar_id) of the default calendar."""
        account = self.defaults.account_id
        if not account:
            for org in self.config.organizations:
                for pa in org.provider_accounts:
                    if pa.provider_type == "calendar" and pa.provider_name == self.defaults.provider:
                        account = pa.account_id
                        break
                if account:
                    break
        if not account:
            raise ValueError("No default calendar configured (calendar_defaults.account_id)")
        return self.defaults.provider, account, self.defaults.calendar_id

    def target_calendar(self) -> tuple[str, str, str]:
        """(provider, account_id, calendar_id) for events found in e-mail.

        Uses email_calendar.target_* when set (chosen in the workspace), else
        the default calendar.
        """
        provider, account, calendar_id = self.default_calendar()
        if self.settings.target_account_id:
            provider = self.settings.target_provider or provider
            account = self.settings.target_account_id
            calendar_id = self.settings.target_calendar_id or "primary"
        return provider, account, calendar_id

    def _calendar_provider(self, provider: str, account: str) -> Any:
        if provider == "google":
            from executive_copilot.providers.calendar.google import GoogleCalendarProvider

            return GoogleCalendarProvider(self.credential_manager, account)
        if provider == "outlook":
            from executive_copilot.providers.calendar.outlook import OutlookCalendarProvider

            return OutlookCalendarProvider(self.credential_manager, account)
        raise ValueError(f"Unknown calendar provider: {provider}")

    def _ignored(self, sender: str) -> bool:
        sender = sender.lower()
        for rule in self.settings.ignore_senders:
            rule = rule.lower()
            if sender == rule or sender.endswith("@" + rule) or sender.endswith("." + rule):
                return True
        return False

    # --- processed-item memory ------------------------------------------

    async def _processed_ids(self, account: str) -> set[str]:
        if self.db_manager is None:
            return set()
        from executive_copilot.database.repositories import ProcessedItemRepository

        async with self.db_manager.session() as session:
            return await ProcessedItemRepository(session).get_processed_ids(ITEM_TYPE, account)

    async def _mark(self, account: str, message_id: str, result: str) -> None:
        if self.db_manager is None:
            return
        from executive_copilot.database.repositories import ProcessedItemRepository

        async with self.db_manager.session() as session:
            repo = ProcessedItemRepository(session)
            if not await repo.is_processed(ITEM_TYPE, message_id, account):
                await repo.mark_processed(ITEM_TYPE, message_id, account, job_name=JOB_NAME, result=result)
                await session.commit()

    # --- review -----------------------------------------------------------

    async def review(
        self,
        days_back: int | None = None,
        account: str | None = None,
        include_processed: bool = False,
    ) -> dict[str, Any]:
        """Return proposed calendar entries found in recent e-mail."""
        from executive_copilot.providers.email.outlook import OutlookProvider

        days = days_back or self.settings.days_back
        accounts = [account] if account else self.source_accounts()
        since = datetime.now(UTC) - timedelta(days=days)
        provider_name, cal_account, cal_id = self.target_calendar()

        result: dict[str, Any] = {
            "default_calendar": {"provider": provider_name, "account": cal_account, "calendar_id": cal_id},
            "days_back": days,
            "accounts": accounts,
            "scanned": 0,
            "proposals": [],
            "errors": [],
            "ai_used": self._ai is not None,
        }

        for acc in accounts:
            try:
                outlook = OutlookProvider(self.credential_manager, acc)
                messages = await outlook.list_recent_messages(since, max_results=self.settings.max_emails)
            except Exception as e:
                result["errors"].append({"account": acc, "error": str(e)})
                continue

            done = set() if include_processed else await self._processed_ids(acc)
            candidates = [
                m for m in messages
                if m.get("id") not in done
                and not self._ignored(((m.get("from") or {}).get("emailAddress") or {}).get("address", ""))
            ]
            result["scanned"] += len(candidates)

            flagged = await self._triage(candidates)
            for msg in flagged[:15]:
                try:
                    proposal = await self._extract(outlook, msg, acc)
                except Exception as e:
                    result["errors"].append({"account": acc, "subject": msg.get("subject"), "error": str(e)})
                    continue
                if proposal:
                    result["proposals"].append(proposal)

        result["proposals"], result["filtered_out"] = self._filter(result["proposals"])
        await self._flag_existing(result["proposals"], provider_name, cal_account, cal_id, result["errors"])
        result["proposals"].sort(key=lambda p: p.get("start") or "9999")
        return result

    def _filter(self, proposals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Drop past-dated and low-confidence proposals."""
        today = datetime.now(self.tz).date().isoformat()
        keep = []
        for p in proposals:
            start = (p.get("start") or "")[:10]
            if start and start < today:
                continue
            try:
                confidence = float(p.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            if p.get("start") and confidence < self.settings.min_confidence:
                continue
            keep.append(p)
        return keep, len(proposals) - len(keep)

    async def _triage(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pick messages that imply a calendar entry."""
        invites = [m for m in messages if "eventMessage" in (m.get("@odata.type") or "")]
        rest = [m for m in messages if m not in invites]
        if not rest:
            return invites

        if self._ai is not None:
            from executive_copilot.services.ai import AITextRequest

            lines = []
            for i, m in enumerate(rest):
                sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
                preview = re.sub(r"\s+", " ", m.get("bodyPreview") or "")[:220]
                lines.append(f"{i} | {sender} | {m.get('subject') or ''} | {preview}")
            try:
                reply = await self._ai.generate_text(AITextRequest(
                    input=TRIAGE_PROMPT.format(
                        today=datetime.now(self.tz).date().isoformat(),
                        tz=self.defaults.timezone,
                        emails="\n".join(lines),
                    ),
                    max_output_tokens=400,
                    temperature=0,
                    premium=True,
                ))
                picked = parse_ai_json(reply.text)
                if isinstance(picked, list):
                    idx = {int(i) for i in picked if str(i).isdigit() and int(i) < len(rest)}
                    return invites + [rest[i] for i in sorted(idx)]
            except Exception as e:
                logger.warning("AI triage failed; using keywords", error=str(e))

        return invites + [
            m for m in rest if KEYWORDS.search(f"{m.get('subject') or ''} {m.get('bodyPreview') or ''}")
        ]

    async def _extract(self, outlook: Any, msg: dict[str, Any], account: str) -> dict[str, Any] | None:
        raw = await outlook.get_message_raw(msg["id"])
        sender = ((raw.get("from") or {}).get("emailAddress") or {}).get("address", "")
        received = raw.get("receivedDateTime") or msg.get("receivedDateTime") or ""

        fields = graph_event_fields(raw, self.tz)
        if fields is None and self._ai is not None:
            from executive_copilot.services.ai import AITextRequest

            body_data = raw.get("body") or {}
            body = body_data.get("content") or ""
            if body_data.get("contentType") == "html":
                body = html_to_text(body)
            reply = await self._ai.generate_text(AITextRequest(
                input=EXTRACT_PROMPT.format(
                    today=datetime.now(self.tz).date().isoformat(),
                    tz=self.defaults.timezone,
                    received=received,
                    sender=sender,
                    subject=raw.get("subject") or "",
                    body=body[:8000],
                ),
                max_output_tokens=500,
                temperature=0,
            ))
            data = parse_ai_json(reply.text)
            fields = data if isinstance(data, dict) else None
        if fields is None:
            fields = {
                "needs_calendar": True,
                "title": raw.get("subject") or "",
                "start": None,
                "end": None,
                "location": None,
                "notes": None,
                "confidence": 0.3,
                "reason": "Keyword match; date not extracted (server AI unavailable)",
            }
        if not fields.get("needs_calendar"):
            return None

        return {
            "message_id": msg["id"],
            "account": account,
            "from": sender,
            "subject": raw.get("subject"),
            "received_at": received,
            "web_link": raw.get("webLink"),
            "title": fields.get("title") or raw.get("subject"),
            "start": fields.get("start"),
            "end": fields.get("end"),
            "location": fields.get("location"),
            "notes": fields.get("notes"),
            "confidence": fields.get("confidence"),
            "reason": fields.get("reason"),
            "already_on_calendar": False,
            "matching_event": None,
        }

    async def _flag_existing(
        self,
        proposals: list[dict[str, Any]],
        provider_name: str,
        account: str,
        calendar_id: str,
        errors: list[dict[str, Any]],
    ) -> None:
        """Mark proposals whose event already exists on the default calendar."""
        from executive_copilot.providers.calendar.interface import EventQuery

        dated = []
        for p in proposals:
            try:
                if p.get("start"):
                    dated.append((p, parse_local(p["start"], self.tz)[0]))
            except ValueError:
                p["start_invalid"] = True
        if not dated:
            return
        lo = min(d for _, d in dated) - timedelta(days=1)
        hi = max(d for _, d in dated) + timedelta(days=2)
        try:
            provider = self._calendar_provider(provider_name, account)
            ids = [] if (provider_name == "outlook" and calendar_id == "primary") else [calendar_id]
            events, _ = await provider.get_events(EventQuery(
                start_time=lo, end_time=hi, calendar_ids=ids, limit=500,
            ))
        except Exception as e:
            errors.append({"account": account, "error": f"Could not check existing events: {e}"})
            return
        for p, start in dated:
            for ev in events:
                same_day = ev.start_time.astimezone(self.tz).date() == start.astimezone(self.tz).date()
                if same_day and titles_match(ev.title, p["title"] or ""):
                    p["already_on_calendar"] = True
                    p["matching_event"] = {"id": ev.provider_ref.external_id if ev.provider_ref else str(ev.id), "title": ev.title, "start": ev.start_time.isoformat()}
                    break

    # --- create / dismiss -------------------------------------------------

    async def create_event(
        self,
        title: str,
        start: str,
        end: str | None = None,
        location: str | None = None,
        description: str | None = None,
        private: bool = False,
        calendar_account: str | None = None,
        calendar_id: str | None = None,
        attendees: list[str] | None = None,
        reminders: list[int] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Create an event on the default calendar (or the one given)."""
        from executive_copilot.models.calendar import Attendee, EventLocation
        from executive_copilot.providers.calendar.interface import NewEvent

        default_provider, default_account, default_cal = self.default_calendar()
        provider_name = provider or default_provider
        account = calendar_account or default_account
        cal_id = calendar_id or (default_cal if account == default_account else "primary")

        start_dt, all_day = parse_local(start, self.tz)
        if end:
            end_dt, _ = parse_local(end, self.tz)
            if all_day:
                end_dt = end_dt + timedelta(days=1)  # all-day end is exclusive
        elif all_day:
            end_dt = start_dt + timedelta(days=1)
        else:
            end_dt = start_dt + timedelta(minutes=self.defaults.default_duration_minutes)
        if end_dt <= start_dt:
            raise ValueError("Event end must be after start")

        is_link = bool(location and re.match(r"https?://", location))
        new_event = NewEvent(
            title=title,
            description=description,
            start_time=start_dt.replace(tzinfo=None) if not all_day else start_dt,
            end_time=end_dt.replace(tzinfo=None) if not all_day else end_dt,
            is_all_day=all_day,
            timezone=self.defaults.timezone,
            location=(
                EventLocation(display_name=location, conference_url=location if is_link else None)
                if location
                else None
            ),
            attendees=[Attendee(email=a) for a in (attendees or [])],
            send_notifications=bool(attendees),
            # Outlook addresses the default calendar without an ID.
            calendar_id=None if (provider_name == "outlook" and cal_id == "primary") else cal_id,
            visibility="private" if private else "default",
            reminders=reminders or [],
        )
        calendar = self._calendar_provider(provider_name, account)
        created = await calendar.create_event(new_event)
        ref = created.provider_ref
        return {
            "id": ref.external_id if ref else str(created.id),
            "title": created.title,
            "start": created.start_time.isoformat(),
            "end": created.end_time.isoformat(),
            "all_day": all_day,
            "calendar": {"provider": provider_name, "account": account, "calendar_id": cal_id},
            "private": private,
            "url": ref.external_url if ref else None,
        }

    async def add_to_calendar(
        self,
        message_id: str,
        account: str,
        title: str,
        start: str,
        end: str | None = None,
        location: str | None = None,
        notes: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        web_link: str | None = None,
    ) -> dict[str, Any]:
        """Create the event for a reviewed e-mail and remember it."""
        origin = f"Origen: correo de {sender or 'desconocido'}"
        if subject:
            origin += f" — «{subject}»"
        origin += f" ({account})"
        if web_link:
            origin += f"\n{web_link}"
        description = f"{notes}\n\n{origin}" if notes else origin

        org_type = (self._org_type_for(account) or "").lower()
        private = self.defaults.personal_events_private and org_type in ("personal", "school")

        provider_name, cal_account, cal_id = self.target_calendar()
        event = await self.create_event(
            title=title, start=start, end=end, location=location, description=description, private=private,
            provider=provider_name, calendar_account=cal_account, calendar_id=cal_id,
        )
        await self._mark(account, message_id, f"created:{event['id']}")
        return {"created": event, "message_id": message_id}

    async def dismiss(self, message_id: str, account: str) -> dict[str, Any]:
        """Never propose this e-mail again."""
        await self._mark(account, message_id, "dismissed")
        return {"dismissed": message_id, "account": account}
