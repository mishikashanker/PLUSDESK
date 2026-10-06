"""Prepare two genuine synthetic reports before the live third-report demo.

Run only against your local MODE=demo database, before today's digest is frozen.
No external messages are sent and no existing data is deleted.
"""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from app.config import Settings
from app.contracts import Submission
from app.database import build_engine, session_factory
from app.models import DailyDigest, User
from app.repository import StandupRepository


def main():
    os.chdir(Path(__file__).resolve().parent)
    settings = Settings()
    if settings.mode != 'demo':
        raise SystemExit('This helper is for the local offline demo only.')
    Path('data').mkdir(exist_ok=True)
    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(settings.database_url)
    sessions = session_factory(engine)
    repository = StandupRepository(sessions, settings)
    now = datetime.now(ZoneInfo(settings.team_timezone))
    try:
        with sessions() as session:
            frozen = session.scalar(select(DailyDigest.id).where(DailyDigest.workspace_id == settings.slack_team_id,
                DailyDigest.channel_id == settings.standup_channel_id, DailyDigest.digest_date == now.date()))
        if frozen:
            raise SystemExit('Today’s snapshot already exists. Use a new rehearsal DATABASE_URL in .env and restart; do not delete your saved database.')
        for slug, name, completed, next_step in [
            ('arjun', 'Arjun Mehta', 'Finished webhook retry checks.', 'Deploy the staging API.'),
            ('noah', 'Noah Williams', 'Prepared integration test cases.', 'Run the staging integration tests.')]:
            repository.accept(Submission('web', settings.slack_team_id, settings.standup_channel_id, slug,
                'command', f'judge-demo:{now.date()}:{slug}', None,
                f'Completed: {completed}\nNext: {next_step}\nBlockers: Waiting on staging credentials.', now))
            with sessions.begin() as session:
                user = session.scalar(select(User).where(User.platform == 'web',User.workspace_id == settings.slack_team_id,
                    User.external_user_id == slug))
                user.display_name = name
        print('Prepared two separate, clearly synthetic reports. Re-running this helper reuses their source IDs.')
        print('During the demo, submit a third update as Maya with Blockers: Waiting on staging credentials.')
        print('Then generate today’s digest. Open Task board: the blocker should show 3 reports / 2 repeats.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
