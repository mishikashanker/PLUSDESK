"""Durable digest/outbox state machine shared by HTTP triggers and the worker."""
import hashlib
import json
from datetime import date, timedelta
from uuid import uuid4
import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from app.config import Settings
from app.llm_service import Extractor, Source, verify
from app.models import DailyDigest, DigestSource, StandupEntry, TrackerSync, TaskOccurrence, utcnow
from app.tracker_client import ReviewRequired, RetryLater, Tracker


def source_payload(evidence, sources: dict[str, Source]) -> list[dict]:
    return [dict(entry_id=e.entry_id, quote=e.quote, source_id=sources[e.entry_id].source_id,
                 source_url=sources[e.entry_id].source_url) for e in evidence]


def render_digest(day: date, output: dict) -> str:
    lines = [f'Daily standup · {day.isoformat()}', '']
    for key, title in [('progress','Progress'),('blockers','Blockers'),('action_items','Next steps')]:
        lines += [title]
        if not output[key]:
            lines.append('• No items reported.')
        for claim in output[key]:
            safe = claim['text'].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
            lines.append('• '+safe)
            for source in claim['evidence']:
                lines.append('  Source: '+(source['source_url'] or source['source_id']))
        lines.append('')
    return '\n'.join(lines)


class DigestService:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings, extractor: Extractor,
                 tracker: Tracker, client: httpx.AsyncClient):
        self.sessions, self.settings, self.extractor = sessions, settings, extractor
        self.tracker, self.client = tracker, client

    def scope(self, model):
        return (model.workspace_id == self.settings.slack_team_id,
                model.channel_id == self.settings.standup_channel_id)

    def request_digest(self, day: date) -> str:
        """Freeze entry membership once. Repeated cron/UI triggers reuse the row."""
        def existing():
            with self.sessions() as s:
                return s.scalar(select(DailyDigest).where(*self.scope(DailyDigest),
                    DailyDigest.platform == 'team', DailyDigest.digest_date == day))
        row = existing()
        if row:
            return row.id
        cutoff = utcnow()
        try:
            with self.sessions.begin() as s:
                entries = list(s.scalars(select(StandupEntry).where(*self.scope(StandupEntry),
                    StandupEntry.platform.in_(['web','slack']), StandupEntry.standup_date == day,
                    StandupEntry.created_at <= cutoff).order_by(StandupEntry.created_at).limit(101)))
                if not entries:
                    raise ValueError('Submit an update before generating this day’s digest.')
                if len(entries) > 100:
                    raise ValueError('This edition supports up to 100 updates per digest.')
                digest = DailyDigest(platform='team', workspace_id=self.settings.slack_team_id,
                    channel_id=self.settings.standup_channel_id, digest_date=day,
                    timezone_name=self.settings.team_timezone, cutoff_at=cutoff,
                    input_hash=hashlib.sha256(json.dumps([(e.id,e.raw_text) for e in entries]).encode()).hexdigest())
                s.add(digest); s.flush()
                for entry in entries:
                    s.add(DigestSource(digest_id=digest.id, entry_id=entry.id, entry_platform=entry.platform,
                        platform='team', workspace_id=entry.workspace_id, channel_id=entry.channel_id))
                identity = digest.id
            return identity
        except IntegrityError:
            row = existing()
            if row:
                return row.id
            raise

    def claim_digest(self, identity: str) -> str | None:
        now, token = utcnow(), str(uuid4())
        eligible = or_(DailyDigest.status.in_(['pending','failed']),
                       and_(DailyDigest.status == 'processing', DailyDigest.lease_until < now))
        with self.sessions.begin() as s:
            result = s.execute(update(DailyDigest).where(DailyDigest.id == identity,
                *self.scope(DailyDigest), eligible, DailyDigest.attempts < 3,
                or_(DailyDigest.next_attempt_at.is_(None), DailyDigest.next_attempt_at <= now))
                .values(status='processing', lease_token=token, lease_until=now+timedelta(seconds=120),
                        attempts=DailyDigest.attempts+1, error_code=None))
            return token if result.rowcount == 1 else None

    async def process_digest(self, identity: str):
        token = self.claim_digest(identity)
        if not token:
            return
        try:
            with self.sessions() as s:
                entries = list(s.scalars(select(StandupEntry).join(DigestSource,
                    DigestSource.entry_id == StandupEntry.id).where(DigestSource.digest_id == identity,
                    *self.scope(StandupEntry))).all())
                sources = [Source(e.id,e.raw_text,e.user_id,e.source_id,e.source_url) for e in entries]
            extracted = verify(await self.extractor.extract(sources), sources)
            mapped = {s.id:s for s in sources}
            output = {category:[dict(text=c.text,evidence=source_payload(c.evidence,mapped)) for c in getattr(extracted,category)]
                      for category in ('progress','blockers','action_items')}
            with self.sessions.begin() as s:
                claimed = s.execute(update(DailyDigest).where(DailyDigest.id == identity,
                    DailyDigest.status == 'processing', DailyDigest.lease_token == token,
                    DailyDigest.lease_until > utcnow()).values(status='ready', structured_output=output,
                        model_name='offline-extractive-demo' if self.settings.mode == 'demo' else self.settings.openai_model,
                        prompt_version='evidence-v1', lease_token=None, lease_until=None))
                if claimed.rowcount != 1:
                    return
                digest = s.get(DailyDigest, identity)
                digest.rendered_text = render_digest(digest.digest_date, output)
                for category in ('blockers','action_items'):
                    for item in output[category]:
                        key = hashlib.sha256(json.dumps([category,sorted(set(' '.join(e['quote'].lower().split()) for e in item['evidence']))]).encode()).hexdigest()
                        target = 'demo-board' if self.settings.mode == 'demo' else self.settings.jira_project_key
                        existing = s.scalar(select(TrackerSync).where(TrackerSync.item_key == key,
                            *self.scope(TrackerSync), TrackerSync.platform == 'team', TrackerSync.tracker_target == target))
                        if existing is None:
                            existing = TrackerSync(digest_id=identity, platform='team', workspace_id=self.settings.slack_team_id,
                                channel_id=self.settings.standup_channel_id, tracker_target=target, item_key=key,
                                payload=dict(category=category,**item))
                            s.add(existing)
                            s.flush()
                        else:
                            # Exact repeated evidence wording updates its mapped ticket.
                            # Never replace payloads while a remote call is in flight or
                            # has an ambiguous result; those records stay held for review.
                            s.execute(update(TrackerSync).where(TrackerSync.id == existing.id,
                                TrackerSync.status.in_(['synced','pending','failed']))
                                .values(digest_id=identity,payload=dict(category=category,**item),
                                        status='pending',attempts=0,error_code=None,next_attempt_at=None))
                        # Each original submission counts once, even if the LLM
                        # repeats a claim or cron replays this frozen snapshot.
                        for evidence in item['evidence']:
                            seen = s.scalar(select(TaskOccurrence.id).where(
                                TaskOccurrence.task_id == existing.id, TaskOccurrence.entry_id == evidence['entry_id']))
                            if seen is None:
                                entry = s.get(StandupEntry, evidence['entry_id'])
                                s.add(TaskOccurrence(task_id=existing.id, digest_id=identity,
                                    entry_id=entry.id, entry_platform=entry.platform, platform='team',
                                    workspace_id=entry.workspace_id, channel_id=entry.channel_id, quote=evidence['quote']))
        except Exception as error:
            with self.sessions.begin() as s:
                s.execute(update(DailyDigest).where(DailyDigest.id == identity,
                    DailyDigest.status == 'processing', DailyDigest.lease_token == token)
                    .values(status='failed', error_code=type(error).__name__, lease_token=None, lease_until=None,
                            next_attempt_at=utcnow()+timedelta(seconds=30)))

    async def publish_digest(self, identity: str):
        now, token = utcnow(), str(uuid4())
        with self.sessions.begin() as s:
            result = s.execute(update(DailyDigest).where(DailyDigest.id == identity, *self.scope(DailyDigest),
                DailyDigest.status == 'ready', or_(DailyDigest.next_attempt_at.is_(None), DailyDigest.next_attempt_at <= now))
                .values(status='publishing',lease_token=token,lease_until=now+timedelta(seconds=60)))
            if result.rowcount != 1:
                return
            digest = s.get(DailyDigest, identity)
            rendered = digest.rendered_text
        try:
            message_id = 'local:'+identity
            if self.settings.mode == 'live' and self.settings.publish_to_slack:
                response = await self.client.post('https://slack.com/api/chat.postMessage',
                    headers={'Authorization':'Bearer '+self.settings.slack_bot_token.get_secret_value()},
                    json={'channel':self.settings.standup_channel_id, 'text':rendered,
                          'client_msg_id':identity,'unfurl_links':False,'unfurl_media':False}, timeout=15)
                if response.status_code == 429:
                    raise RetryLater()
                response.raise_for_status()
                body = response.json()
                if not body.get('ok'):
                    raise ReviewRequired('Slack declined publication')
                message_id = body['ts']
            with self.sessions.begin() as s:
                s.execute(update(DailyDigest).where(DailyDigest.id == identity, DailyDigest.lease_token == token,
                    DailyDigest.status == 'publishing').values(status='published',posted_message_id=message_id,
                    published_at=utcnow(),lease_token=None,lease_until=None))
        except Exception as error:
            retry = isinstance(error, RetryLater)
            with self.sessions.begin() as s:
                s.execute(update(DailyDigest).where(DailyDigest.id == identity, DailyDigest.lease_token == token)
                    .values(status='ready' if retry else 'review',error_code=type(error).__name__,
                            next_attempt_at=utcnow()+timedelta(seconds=60),lease_token=None,lease_until=None))

    async def sync_task(self, identity: str):
        now, token = utcnow(), str(uuid4())
        with self.sessions.begin() as s:
            result = s.execute(update(TrackerSync).where(TrackerSync.id == identity,*self.scope(TrackerSync),
                TrackerSync.status.in_(['pending','failed']),TrackerSync.attempts < 3,
                or_(TrackerSync.next_attempt_at.is_(None),TrackerSync.next_attempt_at <= now))
                .values(status='processing',lease_token=token,lease_until=now+timedelta(seconds=60),
                        attempts=TrackerSync.attempts+1))
            if result.rowcount != 1:
                return
            task = s.get(TrackerSync, identity)
            payload, external = dict(task.payload), task.external_ticket_id
            if task.deadline_updated_at is not None:
                payload['due_date'] = task.due_date.isoformat() if task.due_date else None
        try:
            ticket = await self.tracker.upsert(identity,payload,external)
            with self.sessions.begin() as s:
                s.execute(update(TrackerSync).where(TrackerSync.id == identity,
                    TrackerSync.status == 'processing',TrackerSync.lease_token == token)
                    .values(status='synced',external_ticket_id=ticket.id,external_ticket_url=ticket.url,
                            error_code=None,lease_token=None,lease_until=None))
        except Exception as error:
            review = isinstance(error, ReviewRequired)
            with self.sessions.begin() as s:
                s.execute(update(TrackerSync).where(TrackerSync.id == identity,TrackerSync.lease_token == token)
                    .values(status='review' if review else 'failed',error_code=type(error).__name__,
                            next_attempt_at=utcnow()+timedelta(seconds=30),lease_token=None,lease_until=None))

    def recover_ambiguous(self):
        with self.sessions.begin() as s:
            s.execute(update(DailyDigest).where(*self.scope(DailyDigest),DailyDigest.status == 'processing',
                DailyDigest.lease_until < utcnow(),DailyDigest.attempts >= 3)
                .values(status='failed',error_code='ExpiredExtractionLease',lease_token=None,lease_until=None))
            for model, status in [(DailyDigest,'publishing'),(TrackerSync,'processing')]:
                s.execute(update(model).where(*self.scope(model),model.status == status,model.lease_until < utcnow())
                    .values(status='review',error_code='ExpiredRemoteLease',lease_token=None,lease_until=None))
