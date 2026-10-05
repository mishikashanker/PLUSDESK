from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.contracts import Receipt, Submission
from app.models import StandupEntry, User


class IntakeRejected(ValueError):
    """Safe user-facing validation failure; never include raw text."""


class StandupRepository:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings):
        self.sessions = sessions
        self.settings = settings

    def accept(self, item: Submission) -> Receipt:
        """Commit before returning; DB uniqueness arbitrates concurrent retries."""
        if item.platform not in ("slack", "web") or (item.workspace_id, item.channel_id) != (
            self.settings.slack_team_id, self.settings.standup_channel_id,
        ):
            raise IntakeRejected("Submission is outside the configured standup scope.")
        if item.input_kind == "message" and not self.settings.collect_channel_messages:
            raise IntakeRejected("Channel collection is disabled. Use /standup.")
        text = item.raw_text.strip()
        if not text or len(text) > self.settings.max_update_chars:
            raise IntakeRejected(f"Provide 1–{self.settings.max_update_chars} characters after /standup.")
        if item.submitted_at.tzinfo is None:
            raise IntakeRejected("Timezone-aware submission timestamp required.")
        if not item.external_user_id or not item.source_id or len(item.source_id) > 200:
            raise IntakeRejected("Submission identity is missing or invalid.")
        identity = (User.platform == item.platform, User.workspace_id == item.workspace_id,
                    User.external_user_id == item.external_user_id)
        source = (StandupEntry.platform == item.platform,
                  StandupEntry.workspace_id == item.workspace_id, StandupEntry.source_id == item.source_id)
        # Retry only a unique-race once, and re-read the winning row.
        for attempt in range(2):
            try:
                with self.sessions.begin() as session:
                    existing = session.scalar(select(StandupEntry).where(*source))
                    if existing is not None:
                        return Receipt(existing.id, True)
                    user = session.scalar(select(User).where(*identity))
                    if user is None:
                        user = User(platform=item.platform, workspace_id=item.workspace_id,
                                    external_user_id=item.external_user_id)
                        session.add(user)
                        session.flush()
                    entry = StandupEntry(
                        user_id=user.id, platform=item.platform, workspace_id=item.workspace_id,
                        channel_id=item.channel_id, input_kind=item.input_kind,
                        source_id=item.source_id, source_url=item.source_url, raw_text=text,
                        submitted_at=item.submitted_at,
                        standup_date=item.submitted_at.astimezone(ZoneInfo(self.settings.team_timezone)).date(),
                    )
                    session.add(entry)
                    session.flush()
                    receipt = Receipt(entry.id, False)
                return receipt
            except IntegrityError:
                if attempt == 1:
                    raise
        raise RuntimeError("Unreachable")
