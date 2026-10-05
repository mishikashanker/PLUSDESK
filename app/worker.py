"""Run standalone in live mode. DB rows, rather than process memory, own work."""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from sqlalchemy import select
from app.config import Settings
from app.database import build_engine, session_factory
from app.digest_service import DigestService
from app.llm_service import DemoExtractor, OpenAIExtractor
from app.models import DailyDigest, TrackerSync
from app.tracker_client import DemoTracker, JiraTracker


def build_service(sessions, settings: Settings, client: httpx.AsyncClient) -> DigestService:
    extractor = DemoExtractor() if settings.mode == 'demo' else OpenAIExtractor(settings,client)
    tracker = DemoTracker() if settings.mode == 'demo' else JiraTracker(settings,client)
    return DigestService(sessions,settings,extractor,tracker,client)


async def tick(service: DigestService):
    settings = service.settings
    local = datetime.now(ZoneInfo(settings.team_timezone))
    if (local.hour,local.minute) >= (settings.digest_hour,settings.digest_minute):
        try:
            service.request_digest(local.date())
        except ValueError:
            pass  # No submissions today: no empty digest.
    service.recover_ambiguous()
    with service.sessions() as s:
        digests = list(s.scalars(select(DailyDigest.id).where(*service.scope(DailyDigest),
            DailyDigest.status.in_(['pending','failed','processing'])).limit(10)))
    for identity in digests:
        await service.process_digest(identity)
    with service.sessions() as s:
        ready = list(s.scalars(select(DailyDigest.id).where(*service.scope(DailyDigest),DailyDigest.status == 'ready').limit(10)))
    for identity in ready:
        await service.publish_digest(identity)
    with service.sessions() as s:
        tasks = list(s.scalars(select(TrackerSync.id).where(*service.scope(TrackerSync),
            TrackerSync.status.in_(['pending','failed'])).limit(30)))
    for identity in tasks:
        await service.sync_task(identity)


async def run(service: DigestService):
    while True:
        try:
            await tick(service)
        except Exception as error:
            logging.getLogger('pulsedesk.worker').error('worker_error type=%s',type(error).__name__)
        await asyncio.sleep(2)


async def main():
    settings = Settings()
    engine = build_engine(settings.database_url)
    try:
        async with httpx.AsyncClient() as client:
            await run(build_service(session_factory(engine),settings,client))
    finally:
        engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
