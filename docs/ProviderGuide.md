# Provider Guide

## Overview

Providers are the interface between Executive Copilot and external systems. They are responsible for:

1. Communicating with external APIs
2. Mapping external data formats to domain models
3. Handling authentication and connection management
4. Translating external errors to domain exceptions

**Providers must never contain business logic.** All business logic belongs in the service layer.

## Provider Types

| Type | Description | Interface |
|------|-------------|-----------|
| Email | Email systems (Outlook, Gmail) | `EmailProvider` |
| Calendar | Calendar systems (Outlook, Google) | `CalendarProvider` |
| Task | Task management (Asana, Todoist) | `TaskProvider` |
| Document | Cloud storage (Drive, OneDrive) | `DocumentProvider` |
| News | News sources (RSS, APIs) | `NewsProvider` |
| Search | Search engines | `SearchProvider` |
| Auth | Authentication | `AuthenticationProvider` |

## Base Provider Interface

All providers extend the base `Provider` class:

```python
class Provider(ABC, Generic[T]):
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    @property
    @abstractmethod
    def info(self) -> ProviderInfo:
        """Provider metadata and capabilities."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check connection health."""
        ...
```

## Provider Info

Every provider declares its capabilities:

```python
@property
def info(self) -> ProviderInfo:
    return ProviderInfo(
        name="outlook",
        display_name="Microsoft Outlook",
        provider_type="email",
        version="1.0.0",
        capabilities=[
            ProviderCapability.READ_EMAILS,
            ProviderCapability.SEND_EMAILS,
            ProviderCapability.SEARCH_EMAILS,
            ProviderCapability.MANAGE_LABELS,
        ],
        requires_auth=True,
        auth_type="oauth2",
    )
```

## Implementing an Email Provider

```python
from executive_copilot.providers.email import EmailProvider, EmailQuery
from executive_copilot.models import Email, Thread

class OutlookEmailProvider(EmailProvider):
    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: GraphClient | None = None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="outlook",
            display_name="Microsoft Outlook",
            provider_type="email",
            capabilities=[...],
        )

    async def connect(self) -> None:
        # Initialize Microsoft Graph client
        self._client = await self._create_client()

    async def disconnect(self) -> None:
        self._client = None

    async def health_check(self) -> bool:
        try:
            await self._client.me.get()
            return True
        except Exception:
            return False

    async def get_inbox(
        self,
        limit: int = 50,
        unread_only: bool = False,
    ) -> list[Email]:
        # Fetch from Microsoft Graph
        messages = await self._client.me.messages.get(top=limit)

        # Map to domain models
        return [self._map_to_email(msg) for msg in messages]

    def _map_to_email(self, msg: Message) -> Email:
        """Map Microsoft Graph message to Email domain model."""
        return Email(
            id=uuid4(),
            sender=EmailAddress(
                address=msg.from_.email_address.address,
                display_name=msg.from_.email_address.name,
            ),
            to_recipients=[
                EmailAddress(address=r.email_address.address)
                for r in msg.to_recipients
            ],
            subject=msg.subject,
            body_text=msg.body.content if msg.body.content_type == "text" else None,
            body_html=msg.body.content if msg.body.content_type == "html" else None,
            received_at=msg.received_date_time,
            is_read=msg.is_read,
            provider_ref=ProviderReference(
                provider_name="outlook",
                external_id=msg.id,
                external_url=msg.web_link,
            ),
        )
```

## Implementing a Calendar Provider

```python
from executive_copilot.providers.calendar import CalendarProvider, EventQuery
from executive_copilot.models import CalendarEvent

class GoogleCalendarProvider(CalendarProvider):
    async def get_events(
        self,
        query: EventQuery,
    ) -> tuple[list[CalendarEvent], str | None]:
        # Fetch from Google Calendar API
        events_result = await self._service.events().list(
            calendarId='primary',
            timeMin=query.start_time.isoformat() + 'Z',
            timeMax=query.end_time.isoformat() + 'Z',
            maxResults=query.limit,
        ).execute()

        events = [
            self._map_to_event(e)
            for e in events_result.get('items', [])
        ]

        next_token = events_result.get('nextPageToken')
        return events, next_token

    def _map_to_event(self, event: dict) -> CalendarEvent:
        """Map Google Calendar event to domain model."""
        return CalendarEvent(
            id=uuid4(),
            title=event.get('summary', 'No Title'),
            description=event.get('description'),
            start_time=self._parse_datetime(event['start']),
            end_time=self._parse_datetime(event['end']),
            is_all_day='date' in event['start'],
            attendees=[
                Attendee(
                    email=a['email'],
                    response_status=self._map_response(a.get('responseStatus')),
                )
                for a in event.get('attendees', [])
            ],
            provider_ref=ProviderReference(
                provider_name="google_calendar",
                external_id=event['id'],
                external_url=event.get('htmlLink'),
            ),
        )
```

## Implementing a Task Provider

```python
from executive_copilot.providers.task import TaskProvider, TaskQuery
from executive_copilot.models import Task, TaskStatus

class AsanaTaskProvider(TaskProvider):
    async def get_tasks(self, query: TaskQuery) -> list[Task]:
        # Fetch from Asana API
        tasks_data = await self._client.tasks.find_all(
            project=query.project_id,
            assignee='me',
            completed_since='now' if not query.include_completed else None,
        )

        return [self._map_to_task(t) for t in tasks_data]

    async def complete_task(self, task_id: str) -> Task:
        # Update in Asana
        updated = await self._client.tasks.update(
            task_id,
            {'completed': True}
        )
        return self._map_to_task(updated)

    def _map_to_task(self, task: dict) -> Task:
        return Task(
            id=uuid4(),
            title=task['name'],
            description=task.get('notes'),
            status=TaskStatus.COMPLETED if task['completed'] else TaskStatus.NOT_STARTED,
            due_date=self._parse_date(task.get('due_on')),
            project_id=task.get('projects', [{}])[0].get('gid'),
            project_name=task.get('projects', [{}])[0].get('name'),
            provider_ref=ProviderReference(
                provider_name="asana",
                external_id=task['gid'],
                external_url=task.get('permalink_url'),
            ),
        )
```

## Error Handling

Providers should translate external errors to domain exceptions:

```python
from executive_copilot.exceptions import (
    AuthenticationError,
    ExternalServiceError,
    RateLimitError,
)

class OutlookEmailProvider(EmailProvider):
    async def get_inbox(self, ...) -> list[Email]:
        try:
            return await self._fetch_inbox()
        except AuthError as e:
            raise AuthenticationError(
                "Outlook authentication failed",
                provider="outlook",
                cause=e,
            )
        except RateLimitExceeded as e:
            raise RateLimitError(
                "Outlook rate limit exceeded",
                provider_name="outlook",
                retry_after=e.retry_after,
                cause=e,
            )
        except APIError as e:
            raise ExternalServiceError(
                f"Outlook API error: {e.message}",
                provider_name="outlook",
                status_code=e.status_code,
                cause=e,
            )
```

## Provider Registration

Register providers in the application startup:

```python
from executive_copilot.providers.base import get_provider_registry

def register_providers():
    registry = get_provider_registry()

    # Email providers
    registry.register("email", "outlook", OutlookEmailProvider)
    registry.register("email", "gmail", GmailEmailProvider)

    # Calendar providers
    registry.register("calendar", "outlook", OutlookCalendarProvider)
    registry.register("calendar", "google", GoogleCalendarProvider)

    # Task providers
    registry.register("task", "asana", AsanaTaskProvider)
```

## Provider Configuration

```yaml
# config/providers.yaml
providers:
  - provider_type: "email"
    provider_name: "outlook"
    enabled: true
    account_id: "work"
    oauth_config_name: "microsoft_work"
    settings:
      sync_sent: true
      max_results: 100

  - provider_type: "email"
    provider_name: "gmail"
    enabled: true
    account_id: "personal"
    oauth_config_name: "google_personal"
    settings:
      include_spam: false

oauth:
  microsoft_work:
    provider_name: "microsoft"
    client_id: "${MS_CLIENT_ID}"
    client_secret: "${MS_CLIENT_SECRET}"
    redirect_uri: "http://localhost:8080/callback"
    scopes:
      - "Mail.Read"
      - "Mail.Send"
      - "Calendars.Read"
```

## Testing Providers

```python
@pytest.mark.asyncio
async def test_outlook_get_inbox(mocker):
    # Mock the external client
    mock_client = mocker.Mock()
    mock_client.me.messages.get.return_value = [
        {"id": "1", "subject": "Test", ...}
    ]

    provider = OutlookEmailProvider(config)
    provider._client = mock_client

    emails = await provider.get_inbox(limit=10)

    assert len(emails) == 1
    assert emails[0].subject == "Test"

@pytest.mark.asyncio
async def test_provider_error_translation(mocker):
    provider = OutlookEmailProvider(config)
    mocker.patch.object(
        provider,
        '_fetch_inbox',
        side_effect=RateLimitExceeded(retry_after=60)
    )

    with pytest.raises(RateLimitError) as exc:
        await provider.get_inbox()

    assert exc.value.retry_after == 60
```

## Best Practices

1. **Keep providers thin**: Only data fetching and mapping
2. **Use async/await**: All I/O operations should be async
3. **Handle pagination**: Implement proper pagination for large result sets
4. **Cache appropriately**: Use sync tokens for incremental sync
5. **Log operations**: Log at DEBUG level for troubleshooting
6. **Test with mocks**: Mock external APIs in unit tests
7. **Document rate limits**: Note any provider-specific rate limits
