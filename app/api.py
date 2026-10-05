"""Authenticated dashboard API. All reads are constrained to the configured team."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, or_, update
from app.contracts import Submission
from app.models import DailyDigest, DigestSource, StandupEntry, TrackerSync, User, TaskOccurrence, utcnow
from app.repository import IntakeRejected
from app.seed import PEOPLE, seed_demo

router = APIRouter(prefix='/api')


def signed_in(request: Request):
    return request.app.state.auth.require(request)


class Login(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(max_length=256)


class UpdateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    completed: str = Field(min_length=1,max_length=1200)
    next_step: str = Field(min_length=1,max_length=1200)
    blockers: str = Field(default='None',max_length=1200)
    request_id: UUID
    member: str | None = Field(default=None,max_length=80)
    consent: Literal[True]


class DigestInput(BaseModel):
    day: date


class TaskMove(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['todo','in_progress','done'] | None = None
    due_date: date | None = None


@router.post('/login')
def login(payload: Login, request: Request, response: Response):
    auth = request.app.state.auth
    session = auth.authenticate(request,payload.username,payload.password)
    response.set_cookie('pulse_session',auth.encode(session),max_age=28800,httponly=True,
                        secure=auth.settings.secure_cookies,samesite='strict',path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'user':session['user'],'csrf':session['csrf']}


@router.get('/session')
def session(request: Request, principal=Depends(signed_in)):
    settings = request.app.state.settings
    return {'user':principal['user'],'csrf':principal['csrf'],'mode':settings.mode,
            'team_name':settings.team_name,'timezone':settings.team_timezone,
            'today':datetime.now(ZoneInfo(settings.team_timezone)).date().isoformat(),
            'schedule':f'{settings.digest_hour:02}:{settings.digest_minute:02}',
            'channel':'#daily-standup','channel_id':settings.standup_channel_id,
            'collection':'Approved channel + /standup' if settings.collect_channel_messages else 'Explicit submissions only',
            'members':[{'id':p[0],'name':p[1],'role':p[2]} for p in PEOPLE] if settings.mode == 'demo' else [],
            'integrations':{'llm':'Offline extractive demo' if settings.mode == 'demo' else settings.openai_model,
                'tracker':'Local demo board' if settings.mode == 'demo' else 'Jira · '+settings.jira_project_key,
                'slack':'Local digest only' if settings.mode == 'demo' or not settings.publish_to_slack else 'Slack publication enabled'}}


@router.post('/logout')
def logout(response: Response, _=Depends(signed_in)):
    response.delete_cookie('pulse_session',path='/')
    return {'ok':True}


def entry_json(entry: StandupEntry, user: User) -> dict:
    role = next((p[2] for p in PEOPLE if p[0] == user.external_user_id),'Team member')
    return {'id':entry.id,'name':user.display_name or user.external_user_id,'role':role,
            'text':entry.raw_text,'submitted_at':entry.submitted_at.isoformat(),'source_id':entry.source_id,
            'source_url':entry.source_url,'platform':entry.platform,'day':entry.standup_date.isoformat()}


def task_json(task: TrackerSync, reported_count: int = 0, today: date | None = None) -> dict:
    deadline_status = 'no_deadline'
    if task.due_date:
        deadline_status = ('completed' if task.board_status == 'done' else
                           'overdue' if today and task.due_date < today else
                           'due_today' if task.due_date == today else 'upcoming')
    return {'id':task.id,'text':task.payload['text'],'category':task.payload['category'],
            'evidence':task.payload['evidence'],'status':task.board_status,'sync_status':task.status,
            'ticket_id':task.external_ticket_id,'ticket_url':task.external_ticket_url,'error_code':task.error_code,
            'reported_count':reported_count,'repeat_count':max(0,reported_count-1),
            'due_date':task.due_date.isoformat() if task.due_date else None,'deadline_status':deadline_status,
            'deadline_updated_by':task.deadline_updated_by,
            'deadline_updated_at':task.deadline_updated_at.isoformat() if task.deadline_updated_at else None}


@router.get('/dashboard')
def dashboard(day: date, request: Request, _=Depends(signed_in)):
    service = request.app.state.service
    with service.sessions() as s:
        rows = s.execute(select(StandupEntry,User).join(User,User.id == StandupEntry.user_id)
            .where(*service.scope(StandupEntry),StandupEntry.standup_date == day)
            .order_by(StandupEntry.created_at.desc()).limit(200)).all()
        digest = s.scalar(select(DailyDigest).where(*service.scope(DailyDigest),DailyDigest.platform == 'team',DailyDigest.digest_date == day))
        result = None
        tasks = []
        if digest:
            source_count = s.scalar(select(func.count()).select_from(DigestSource).where(DigestSource.digest_id == digest.id))
            result = {'id':digest.id,'status':digest.status,'output':digest.structured_output,'text':digest.rendered_text,
                'source_count':source_count,'cutoff_at':digest.cutoff_at.isoformat(),'model':digest.model_name,
                'error_code':digest.error_code,'published_at':digest.published_at.isoformat() if digest.published_at else None}
            task_rows = list(s.scalars(select(TrackerSync).where(*service.scope(TrackerSync),
                or_(TrackerSync.digest_id == digest.id, TrackerSync.id.in_(
                    select(TaskOccurrence.task_id).where(TaskOccurrence.digest_id == digest.id,
                                                       *service.scope(TaskOccurrence))))).order_by(TrackerSync.created_at)))
            counts = dict(s.execute(select(TaskOccurrence.task_id,func.count(TaskOccurrence.id))
                .where(TaskOccurrence.task_id.in_([t.id for t in task_rows]),*service.scope(TaskOccurrence))
                .group_by(TaskOccurrence.task_id)).all())
            today = datetime.now(ZoneInfo(service.settings.team_timezone)).date()
            tasks = [task_json(t,counts.get(t.id,0),today) for t in task_rows]
        return {'updates':[entry_json(e,u) for e,u in rows],'digest':result,'tasks':tasks,'day':day.isoformat()}


@router.post('/updates',status_code=201)
def submit(payload: UpdateInput, request: Request, principal=Depends(signed_in)):
    settings, repository = request.app.state.settings, request.app.state.repository
    member = principal['user']
    name = principal['user']
    if settings.mode == 'demo' and payload.member:
        known = {p[0]:p[1] for p in PEOPLE}
        if payload.member not in known:
            raise HTTPException(422,'Choose a demo team member.')
        member, name = payload.member, known[payload.member]
    elif payload.member:
        raise HTTPException(403,'Live updates use your authenticated identity.')
    text = f'Completed: {payload.completed.strip()}\nNext: {payload.next_step.strip()}\nBlockers: {payload.blockers.strip() or "None"}'
    try:
        receipt = repository.accept(Submission('web',settings.slack_team_id,settings.standup_channel_id,
            member,'command','web:'+str(payload.request_id),None,text,utcnow()))
    except IntakeRejected as error:
        raise HTTPException(422,str(error)) from None
    with repository.sessions.begin() as s:
        user = s.scalar(select(User).where(User.platform == 'web',User.workspace_id == settings.slack_team_id,User.external_user_id == member))
        user.display_name = name
    return {'id':receipt.entry_id,'duplicate':receipt.duplicate}


@router.post('/digests',status_code=202)
def generate(payload: DigestInput, request: Request, _=Depends(signed_in)):
    if payload.day > datetime.now(ZoneInfo(request.app.state.settings.team_timezone)).date():
        raise HTTPException(422,'Choose today or an earlier date.')
    try:
        identity = request.app.state.service.request_digest(payload.day)
    except ValueError as error:
        raise HTTPException(422,str(error)) from None
    return {'id':identity,'message':'Daily digest queued, or existing snapshot reused.'}


@router.get('/sources/{identity}')
def source(identity: UUID, request: Request, _=Depends(signed_in)):
    service = request.app.state.service
    with service.sessions() as s:
        row = s.execute(select(StandupEntry,User).join(User,User.id == StandupEntry.user_id)
            .where(StandupEntry.id == str(identity),*service.scope(StandupEntry))).first()
        if row is None:
            raise HTTPException(404,'Source update not found.')
        return entry_json(*row)


@router.get('/digests/{identity}/export')
def export(identity: UUID, request: Request, _=Depends(signed_in)):
    service = request.app.state.service
    with service.sessions() as s:
        digest = s.scalar(select(DailyDigest).where(DailyDigest.id == str(identity),*service.scope(DailyDigest)))
        if not digest or not digest.rendered_text:
            raise HTTPException(404,'Digest is not ready.')
        label = 'Offline extractive demo; no external messages or tickets sent.' if service.settings.mode == 'demo' else 'Evidence-backed team digest.'
        return Response(f'{label}\n\n{digest.rendered_text}',media_type='text/plain',
            headers={'Content-Disposition':f'attachment; filename="pulsedesk-{digest.digest_date}.txt"'})


@router.get('/tasks/{identity}/history')
def task_history(identity: UUID, request: Request, _=Depends(signed_in)):
    service = request.app.state.service
    with service.sessions() as s:
        task = s.scalar(select(TrackerSync).where(TrackerSync.id == str(identity),*service.scope(TrackerSync)))
        if task is None:
            raise HTTPException(404,'Task not found.')
        rows = s.execute(select(TaskOccurrence,StandupEntry,User,DailyDigest)
            .join(StandupEntry,StandupEntry.id == TaskOccurrence.entry_id)
            .join(User,User.id == StandupEntry.user_id)
            .join(DailyDigest,DailyDigest.id == TaskOccurrence.digest_id)
            .where(TaskOccurrence.task_id == task.id,*service.scope(TaskOccurrence),*service.scope(StandupEntry))
            .order_by(StandupEntry.submitted_at,TaskOccurrence.recorded_at,TaskOccurrence.id)).all()
        today = datetime.now(ZoneInfo(service.settings.team_timezone)).date()
        return {'task':task_json(task,len(rows),today),'history':[
            {'id':event.id,'entry_id':entry.id,'name':user.display_name or user.external_user_id,
             'quote':event.quote,'source_id':entry.source_id,'source_url':entry.source_url,
             'reported_at':entry.submitted_at.isoformat(),'recorded_at':event.recorded_at.isoformat(),
             'digest_day':digest.digest_date.isoformat()} for event,entry,user,digest in rows]}


@router.patch('/tasks/{identity}')
def move_task(identity: UUID, payload: TaskMove, request: Request, principal=Depends(signed_in)):
    service = request.app.state.service
    fields = payload.model_fields_set
    if not fields or ('status' in fields and payload.status is None):
        raise HTTPException(422,'Provide a valid task status or deadline.')
    if service.settings.mode != 'demo' and 'status' in fields:
        raise HTTPException(403,'Manage live ticket workflow in Jira. Deadlines can be set here.')
    with service.sessions.begin() as s:
        task = s.scalar(select(TrackerSync).where(TrackerSync.id == str(identity),*service.scope(TrackerSync)))
        if task is None:
            raise HTTPException(404,'Task not found.')
        values = {}
        if 'status' in fields:
            values['board_status'] = payload.status
        if 'due_date' in fields:
            values.update(due_date=payload.due_date,deadline_updated_by=principal['user'],deadline_updated_at=utcnow())
            if service.settings.mode == 'live':
                values.update(status='pending',attempts=0,error_code=None,next_attempt_at=None)
        # Do not race an in-flight sync or override an uncertain remote create.
        changed = s.execute(update(TrackerSync).where(TrackerSync.id == task.id,*service.scope(TrackerSync),
            TrackerSync.status.not_in(['processing','review'])).values(**values))
        if changed.rowcount != 1:
            raise HTTPException(409,'Task sync is processing or needs review. Try again after it settles.')
    return {'ok':True,'message':'Deadline saved and sync queued.' if 'due_date' in fields and service.settings.mode == 'live' else 'Task updated.'}


@router.post('/demo/seed')
def seed(request: Request, _=Depends(signed_in)):
    if request.app.state.settings.mode != 'demo':
        raise HTTPException(403,'Sample data is disabled in live mode.')
    seed_demo(request.app.state.repository)
    return {'ok':True}
