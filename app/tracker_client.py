"""Jira Cloud adapter. Unknown create outcomes never trigger blind duplicates."""
from dataclasses import dataclass
from typing import Protocol
import httpx
from app.config import Settings


@dataclass(frozen=True)
class Ticket:
    id: str
    url: str | None


class RetryLater(Exception):
    pass


class ReviewRequired(Exception):
    pass


class Tracker(Protocol):
    async def upsert(self, item_id: str, payload: dict, existing_id: str | None) -> Ticket: ...


class DemoTracker:
    async def upsert(self, item_id: str, payload: dict, existing_id: str | None) -> Ticket:
        # Local task IDs are never presented as real Jira tickets.
        return Ticket(existing_id or 'DEMO-'+item_id[:8].upper(), None)


def adf(text: str) -> dict:
    return {'version':1, 'type':'doc', 'content':[
        {'type':'paragraph','content':[{'type':'text','text':line or ' '}]} for line in text.splitlines()]}


class JiraTracker:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client
        self.base = settings.jira_base_url.rstrip('/')
        self.auth = httpx.BasicAuth(settings.jira_email, settings.jira_api_token.get_secret_value())

    async def upsert(self, item_id: str, payload: dict, existing_id: str | None) -> Ticket:
        marker = 'pulsedesk-'+item_id
        sources = '\n'.join(f"Source {e['entry_id']}: {e.get('source_url') or e['source_id']}\nQuote: {e['quote']}" for e in payload['evidence'])
        fields = {'summary':payload['text'][:200], 'description':adf(
            f"Async standup {payload['category']}\n\n{payload['text']}\n\n{sources}\n\nReference: {marker}")}
        if 'due_date' in payload:
            # Only explicitly entered dates are sent; null clears a prior deadline.
            fields['duedate'] = payload['due_date']
        if existing_id:
            response = await self.client.put(f'{self.base}/rest/api/3/issue/{existing_id}',
                auth=self.auth, json={'fields':fields}, timeout=15)
            if response.status_code == 429:
                raise RetryLater()
            response.raise_for_status()
            return Ticket(existing_id, f'{self.base}/browse/{existing_id}')
        # The unique outbox key + lease serializes creates. A stable issue label
        # supports operator reconciliation if the provider outcome is ambiguous.
        fields.update({'project':{'key':self.settings.jira_project_key},
                       'issuetype':{'name':self.settings.jira_issue_type}, 'labels':[marker]})
        try:
            response = await self.client.post(f'{self.base}/rest/api/3/issue', auth=self.auth,
                                              json={'fields':fields}, timeout=15)
        except httpx.TransportError:
            raise ReviewRequired('Jira create outcome unknown') from None
        if response.status_code == 429:
            raise RetryLater()
        if response.status_code >= 500:
            raise ReviewRequired('Jira create outcome unknown')
        response.raise_for_status()
        try:
            key = response.json()['key']
        except (KeyError, ValueError):
            raise ReviewRequired('Jira response missing issue identity') from None
        return Ticket(key, f'{self.base}/browse/{key}')
