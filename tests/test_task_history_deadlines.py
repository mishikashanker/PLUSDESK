import asyncio
import json
from datetime import date, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import TaskOccurrence, TrackerSync
from app.tracker_client import JiraTracker
from app.worker import tick
from test_intake import configured, api
from test_product import login, share


def generate(app, client, headers):
    day = client.get('/api/session').json()['today']
    result = client.post('/api/digests', json={'day': day}, headers=headers)
    assert result.status_code == 202
    asyncio.run(tick(app.state.service))
    return day, result.json()['id'], client.get('/api/dashboard', params={'day': day}).json()


def test_three_reports_one_task_and_each_source_logged(api):
    app, client = api
    headers = login(client)
    sources = [share(client, headers, member=m).json()['id'] for m in ['maya', 'arjun', 'noah']]
    day, digest, board = generate(app, client, headers)
    assert len(board['tasks']) == 2
    for task in board['tasks']:
        assert task['reported_count'] == 3 and task['repeat_count'] == 2
        history = client.get('/api/tasks/' + task['id'] + '/history').json()['history']
        assert len(history) == 3 and {h['entry_id'] for h in history} == set(sources)
        assert {h['name'] for h in history} == {'Maya Chen', 'Arjun Mehta', 'Noah Williams'}
        for report in history:
            assert report['reported_at'] and report['recorded_at']
            original = client.get('/api/sources/' + report['entry_id']).json()
            assert report['quote'] in original['text']
    for _ in range(2):
        assert client.post('/api/digests', json={'day': day}, headers=headers).json()['id'] == digest
        asyncio.run(tick(app.state.service))
    with app.state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(TaskOccurrence)) == 6


def test_submission_retry_is_not_a_repeat(api):
    app, client = api
    headers = login(client)
    identity = str(uuid4())
    share(client, headers, request_id=identity)
    share(client, headers, request_id=identity)
    _, _, board = generate(app, client, headers)
    assert all(t['reported_count'] == 1 and t['repeat_count'] == 0 for t in board['tasks'])


def test_deadline_set_change_clear_and_completed_not_overdue(api):
    app, client = api
    headers = login(client)
    share(client, headers)
    day, _, board = generate(app, client, headers)
    endpoint = '/api/tasks/' + board['tasks'][0]['id']
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    assert client.patch(endpoint, json={'due_date': yesterday}, headers=headers).status_code == 200
    task = client.get(endpoint + '/history').json()['task']
    assert task['due_date'] == yesterday and task['deadline_status'] == 'overdue'
    assert task['deadline_updated_by'] == 'admin' and task['deadline_updated_at']
    client.patch(endpoint, json={'status': 'done'}, headers=headers)
    assert client.get(endpoint + '/history').json()['task']['deadline_status'] == 'completed'
    client.patch(endpoint, json={'status': 'todo', 'due_date': day}, headers=headers)
    assert client.get(endpoint + '/history').json()['task']['deadline_status'] == 'due_today'
    tomorrow = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    client.patch(endpoint, json={'due_date': tomorrow}, headers=headers)
    assert client.get(endpoint + '/history').json()['task']['deadline_status'] == 'upcoming'
    client.patch(endpoint, json={'due_date': None}, headers=headers)
    task = client.get(endpoint + '/history').json()['task']
    assert task['due_date'] is None and task['deadline_status'] == 'no_deadline'


def test_deadline_validation_auth_and_missing_task(api):
    app, client = api
    headers = login(client)
    share(client, headers)
    _, _, board = generate(app, client, headers)
    endpoint = '/api/tasks/' + board['tasks'][0]['id']
    for payload in [{'due_date': 'next Tuesday'}, {}, {'status': None}, {'priority': 'secret'}]:
        assert client.patch(endpoint, json=payload, headers=headers).status_code == 422
    assert client.patch(endpoint, json={'due_date': '2026-10-08'}).status_code == 403
    assert client.get('/api/tasks/' + str(uuid4()) + '/history').status_code == 404


@pytest.mark.parametrize('sync_status', ['processing', 'review'])
def test_deadline_cannot_override_active_or_ambiguous_sync(api, sync_status):
    app, client = api
    headers = login(client)
    share(client, headers)
    _, _, board = generate(app, client, headers)
    identity = board['tasks'][0]['id']
    with app.state.sessions.begin() as session:
        session.get(TrackerSync, identity).status = sync_status
    assert client.patch('/api/tasks/' + identity, json={'due_date': '2026-10-08'}, headers=headers).status_code == 409
    with app.state.sessions() as session:
        assert session.get(TrackerSync, identity).due_date is None


def test_occurrence_uniqueness_and_scope_fk(api):
    app, client = api
    headers = login(client)
    share(client, headers)
    generate(app, client, headers)
    with app.state.sessions() as session:
        first = session.scalar(select(TaskOccurrence))
        values = {key: getattr(first, key) for key in ['task_id', 'digest_id', 'entry_id', 'entry_platform', 'platform', 'workspace_id', 'channel_id', 'quote']}
    with pytest.raises(IntegrityError), app.state.sessions.begin() as session:
        session.add(TaskOccurrence(**values))
    values['workspace_id'] = 'TOTHER'
    values['entry_id'] = str(uuid4())
    with pytest.raises(IntegrityError), app.state.sessions.begin() as session:
        session.add(TaskOccurrence(**values))


def test_jira_receives_explicit_due_date_and_clear(configured):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(204)
    settings = configured.model_copy(update={'jira_base_url': 'https://team.atlassian.net', 'jira_email': 'team@example.com'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tracker = JiraTracker(settings, client)
            for due in ['2026-10-08', None]:
                await tracker.upsert('id', {'text': 'Task', 'category': 'blockers', 'evidence': [], 'due_date': due}, 'PULSE-42')
    asyncio.run(run())
    assert calls[0]['fields']['duedate'] == '2026-10-08'
    assert calls[1]['fields']['duedate'] is None


def test_live_deadline_queues_existing_ticket_update(api):
    app, client = api
    headers = login(client)
    share(client, headers)
    _, _, board = generate(app, client, headers)
    identity = board['tasks'][0]['id']
    app.state.service.settings = app.state.service.settings.model_copy(update={'mode': 'live'})
    assert client.patch('/api/tasks/' + identity, json={'due_date': '2026-10-08'}, headers=headers).status_code == 200
    with app.state.sessions() as session:
        task = session.get(TrackerSync, identity)
        assert task.status == 'pending' and task.external_ticket_id
    assert client.patch('/api/tasks/' + identity, json={'status': 'done'}, headers=headers).status_code == 403
