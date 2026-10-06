import asyncio
import json
from datetime import timedelta
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import func, select
from app.llm_service import Claim, Evidence, EvidenceError, Extraction, Source, verify
from app.models import DailyDigest, TrackerSync, utcnow
from app.tracker_client import JiraTracker, ReviewRequired
from app.worker import tick
from test_intake import configured, api


def login(client):
    response=client.post('/api/login',json={'username':'admin','password':'demo-pulsedesk'})
    assert response.status_code==200
    return {'x-pulse-csrf':response.json()['csrf']}


def share(client,headers,**overrides):
    payload=dict(completed='Shipped webhook tests.',next_step='Deploy the staging API.',
        blockers='Waiting on staging credentials.',request_id=str(uuid4()),consent=True)
    payload.update(overrides)
    return client.post('/api/updates',json=payload,headers=headers)


def test_auth_csrf_and_origin(api):
    app,client=api
    assert client.get('/api/session').status_code==401
    assert client.get('/api/dashboard?day=2026-10-07').status_code==401
    assert client.post('/api/login',json={'username':'admin','password':'wrong'}).status_code==401
    headers=login(client)
    assert share(client,{}).status_code==403
    assert share(client,{**headers,'origin':'https://attacker.example'}).status_code==403
    assert share(client,headers).status_code==201
    assert client.post('/api/logout',json={},headers=headers).status_code==200
    assert client.get('/api/session').status_code==401


def test_complete_demo_flow_and_daily_idempotency(api):
    app,client=api
    headers=login(client)
    identity=str(uuid4())
    first=share(client,headers,request_id=identity)
    repeated=share(client,headers,request_id=identity)
    assert first.status_code==201 and repeated.json()['duplicate']
    assert first.json()['id']==repeated.json()['id']
    day=client.get('/api/session').json()['today']
    job=client.post('/api/digests',json={'day':day},headers=headers)
    assert job.status_code==202
    asyncio.run(tick(app.state.service))
    board=client.get('/api/dashboard',params={'day':day}).json()
    assert len(board['updates'])==1
    assert board['digest']['status']=='published'
    assert board['digest']['source_count']==1
    assert len(board['digest']['output']['blockers'])==1
    assert len(board['tasks'])==2 and all(t['sync_status']=='synced' for t in board['tasks'])
    source=client.get('/api/sources/'+first.json()['id']).json()
    assert 'Waiting on staging credentials.' in source['text']
    assert source['source_url'] is None
    repeat_job=client.post('/api/digests',json={'day':day},headers=headers)
    assert repeat_job.json()['id']==job.json()['id']
    asyncio.run(tick(app.state.service))
    second=client.get('/api/dashboard',params={'day':day}).json()
    assert [t['id'] for t in second['tasks']]==[t['id'] for t in board['tasks']]
    exported=client.get('/api/digests/'+job.json()['id']+'/export')
    assert exported.status_code==200 and 'web:'+identity in exported.text
    task=board['tasks'][0]
    assert client.patch('/api/tasks/'+task['id'],json={'status':'done'},headers=headers).status_code==200
    assert share(client,headers,next_step='Run mobile tests.').status_code==201
    late=client.get('/api/dashboard',params={'day':day}).json()
    assert len(late['updates'])==2 and late['digest']['source_count']==1


def test_no_unknown_or_fabricated_evidence():
    source=Source('known','Shipped tests.','member','source',None)
    for entry_id,quote in [('unknown','Shipped tests.'),('known','Invented statement')]:
        output=Extraction(progress=[],blockers=[Claim(text='Claim',evidence=[Evidence(entry_id=entry_id,quote=quote)])],action_items=[])
        with pytest.raises(EvidenceError):
            verify(output,[source])


def test_invalid_extraction_has_no_side_effects(api):
    app,client=api;headers=login(client);share(client,headers)
    day=client.get('/api/session').json()['today']
    identity=client.post('/api/digests',json={'day':day},headers=headers).json()['id']
    class BadExtractor:
        async def extract(self,sources):
            return Extraction(progress=[],blockers=[Claim(text='Invented',evidence=[Evidence(entry_id='unknown',quote='fabricated')])],action_items=[])
    app.state.service.extractor=BadExtractor()
    asyncio.run(app.state.service.process_digest(identity))
    with app.state.sessions() as s:
        row=s.get(DailyDigest,identity)
        assert row.status=='failed' and row.structured_output is None
        assert s.scalar(select(func.count()).select_from(TrackerSync))==0


def test_expired_remote_work_is_held_for_review(api):
    app,client=api;headers=login(client);share(client,headers)
    day=client.get('/api/session').json()['today']
    identity=client.post('/api/digests',json={'day':day},headers=headers).json()['id']
    asyncio.run(app.state.service.process_digest(identity))
    with app.state.sessions.begin() as s:
        row=s.get(DailyDigest,identity);row.status='publishing';row.lease_until=utcnow()-timedelta(seconds=1)
        task=s.scalar(select(TrackerSync));task.status='processing';task.lease_until=utcnow()-timedelta(seconds=1)
    app.state.service.recover_ambiguous()
    with app.state.sessions() as s:
        assert s.get(DailyDigest,identity).status=='review'
        assert s.scalar(select(TrackerSync)).status=='review'


def test_demo_members_require_consent(api):
    _,client=api;headers=login(client)
    assert share(client,headers,consent=False).status_code==422
    assert share(client,headers,member='unknown').status_code==422
    assert share(client,headers,member='maya').status_code==201


def test_demo_seed_is_idempotent(api):
    _,client=api;headers=login(client)
    for _ in range(2):
        assert client.post('/api/demo/seed',json={},headers=headers).status_code==200
    day=client.get('/api/session').json()['today']
    assert len(client.get('/api/dashboard',params={'day':day}).json()['updates'])==5


def test_empty_and_future_day_rejected(api):
    _,client=api;headers=login(client)
    assert client.post('/api/digests',json={'day':'2099-01-01'},headers=headers).status_code==422
    assert client.post('/api/digests',json={'day':'2020-01-01'},headers=headers).status_code==422


def test_jira_create_and_update_use_adf_and_sources(configured):
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(201,json={'key':'PULSE-42'}) if request.method=='POST' else httpx.Response(204)
    settings=configured.model_copy(update={'jira_base_url':'https://team.atlassian.net','jira_email':'team@example.com'})
    payload={'category':'blockers','text':'Need access','evidence':[{'entry_id':'abc','source_id':'command:123','source_url':None,'quote':'Waiting on access'}]}
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tracker=JiraTracker(settings,client)
            first=await tracker.upsert('stable-id',payload,None)
            second=await tracker.upsert('stable-id',payload,first.id)
            assert first.id==second.id=='PULSE-42'
    asyncio.run(run())
    create=json.loads(requests[0].content)
    assert create['fields']['description']['type']=='doc'
    assert 'command:123' in json.dumps(create)
    assert create['fields']['labels']==['pulsedesk-stable-id']
    assert requests[1].method=='PUT'


def test_jira_unknown_create_is_not_blindly_retried(configured):
    def handler(request):
        raise httpx.ReadTimeout('Unknown provider outcome',request=request)
    settings=configured.model_copy(update={'jira_base_url':'https://team.atlassian.net','jira_email':'team@example.com'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ReviewRequired):
                await JiraTracker(settings,client).upsert('id',{'text':'Task','category':'blockers','evidence':[]},None)
    asyncio.run(run())


def test_static_ui_security_headers(api):
    _,client=api
    response=client.get('/')
    assert response.status_code==200 and 'PulseDesk' in response.text
    assert 'frame-ancestors' in response.headers['content-security-policy']
    assert client.get('/static/app.js').status_code==200
    assert client.get('/api/sources/'+str(uuid4())).status_code==401


def test_repeated_findings_update_existing_ticket_across_days(api):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.contracts import Submission
    from app.models import StandupEntry
    app,client=api;headers=login(client)
    share(client,headers)
    today=datetime.now(ZoneInfo(app.state.settings.team_timezone)).date()
    first=app.state.service.request_digest(today)
    asyncio.run(app.state.service.process_digest(first))
    asyncio.run(tick(app.state.service))
    with app.state.sessions() as s:
        tickets={t.item_key:t.external_ticket_id for t in s.scalars(select(TrackerSync))}
    earlier=utcnow()-timedelta(days=1)
    receipt=app.state.repository.accept(Submission('web',app.state.settings.slack_team_id,
        app.state.settings.standup_channel_id,'admin','command','web:older',None,
        'Completed: Shipped webhook tests.\nNext: Deploy the staging API.\nBlockers: Waiting on staging credentials.',earlier))
    with app.state.sessions() as s:
        day=s.get(StandupEntry,receipt.entry_id).standup_date
    second=app.state.service.request_digest(day)
    asyncio.run(app.state.service.process_digest(second))
    with app.state.sessions() as s:
        items=list(s.scalars(select(TrackerSync)))
        assert len(items)==2 and all(t.status=='pending' for t in items)
        assert {t.item_key:t.external_ticket_id for t in items}==tickets
    asyncio.run(tick(app.state.service))
    with app.state.sessions() as s:
        assert all(t.status=='synced' and t.external_ticket_id==tickets[t.item_key] for t in s.scalars(select(TrackerSync)))
