"""Made-up standups; no invented real Slack URLs or real Jira issues."""
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.contracts import Submission
from app.models import User
from app.repository import StandupRepository

PEOPLE = [
    ('maya','Maya Chen','Product design','Finished the onboarding screens and shared the prototype.','Run the first usability session with the team.','Waiting on the final brand assets from marketing.'),
    ('arjun','Arjun Mehta','Backend','Shipped the webhook verification and added retry tests.','Deploy the staging API and smoke-test the endpoints.','Staging credentials are still missing.'),
    ('elena','Elena Rossi','Frontend','Connected the update form to the API.','Complete keyboard navigation and mobile layout checks.','None'),
    ('noah','Noah Williams','Quality','Finished regression checks for the standup intake.','Add end-to-end coverage for digest generation.','The staging API is unavailable for integration tests.'),
    ('sara','Sara Ahmed','Platform','Added the Docker configuration and health probes.','Document the production deployment checklist.','None'),
]


def seed_demo(repository: StandupRepository):
    settings = repository.settings
    if settings.mode != 'demo':
        return
    now = datetime.now(ZoneInfo(settings.team_timezone))
    for slug,name,role,completed,next_step,blocker in PEOPLE:
        repository.accept(Submission('web',settings.slack_team_id,settings.standup_channel_id,slug,'command',
            f'demo:{now.date()}:{slug}',None,f'Completed: {completed}\nNext: {next_step}\nBlockers: {blocker}',now))
        with repository.sessions.begin() as s:
            user = s.scalar(select(User).where(User.platform == 'web',User.workspace_id == settings.slack_team_id,
                                               User.external_user_id == slug))
            user.display_name = name
