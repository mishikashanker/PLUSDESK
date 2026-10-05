from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """One team per deployment. Demo is offline; live disclosure is explicit."""
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    mode: Literal['demo', 'live'] = 'demo'
    database_url: str = 'sqlite:///./data/pulsedesk.db'
    dashboard_user: str = Field(default='admin', min_length=1, max_length=64)
    dashboard_password: SecretStr = SecretStr('demo-pulsedesk')
    session_secret: SecretStr = SecretStr('')
    secure_cookies: bool = False
    team_name: str = Field(default='Orbit Studio', min_length=1, max_length=80)
    team_timezone: str = 'Asia/Kolkata'
    slack_signing_secret: SecretStr = SecretStr('demo-secret')
    slack_bot_token: SecretStr = SecretStr('')
    slack_team_id: str = Field(default='TDEMO', pattern=r'^T[A-Z0-9]+$')
    slack_bot_user_id: str = Field(default='UDEMO', pattern=r'^U[A-Z0-9]+$')
    standup_channel_id: str = Field(default='CDAILY', pattern=r'^[CG][A-Z0-9]+$')
    slack_workspace_domain: str = Field(default='demo-team', pattern=r'^[a-z0-9][a-z0-9-]{0,62}$')
    collect_channel_messages: bool = False
    max_update_chars: int = Field(default=4000, ge=1, le=4000)
    openai_api_key: SecretStr = SecretStr('')
    openai_model: str = 'gpt-4o-mini'
    jira_base_url: str = ''
    jira_email: str = ''
    jira_api_token: SecretStr = SecretStr('')
    jira_project_key: str = Field(default='PULSE', pattern=r'^[A-Z][A-Z0-9_]{1,20}$')
    jira_issue_type: str = 'Task'
    tracker_audience_confirmed: bool = False
    llm_disclosure_confirmed: bool = False
    publish_to_slack: bool = False
    digest_hour: int = Field(default=17, ge=0, le=23)
    digest_minute: int = Field(default=0, ge=0, le=59)
    embedded_worker: bool = True
    seed_demo: bool = True

    @field_validator('team_timezone')
    @classmethod
    def timezone_valid(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode='after')
    def live_policy(self):
        if self.mode == 'live':
            if len(self.session_secret.get_secret_value()) < 32:
                raise ValueError('Live mode requires a 32+ character SESSION_SECRET')
            if len(self.dashboard_password.get_secret_value()) < 12 or self.dashboard_password.get_secret_value() == 'demo-pulsedesk':
                raise ValueError('Set a unique 12+ character DASHBOARD_PASSWORD')
            if not self.secure_cookies:
                raise ValueError('Live mode requires SECURE_COOKIES=true and HTTPS')
            if not self.openai_api_key.get_secret_value() or not self.llm_disclosure_confirmed:
                raise ValueError('Configure OpenAI and confirm LLM disclosure')
            if not self.jira_base_url.startswith('https://') or not self.jira_email or not self.jira_api_token.get_secret_value() or not self.tracker_audience_confirmed:
                raise ValueError('Configure Jira HTTPS credentials and confirm its audience')
            if self.slack_bot_token.get_secret_value() and (self.slack_signing_secret.get_secret_value() == 'demo-secret' or self.slack_team_id == 'TDEMO'):
                raise ValueError('Configure the real Slack workspace and signing secret')
            if self.publish_to_slack and (not self.slack_bot_token.get_secret_value() or self.slack_signing_secret.get_secret_value() == 'demo-secret'):
                raise ValueError('Configure Slack credentials before enabling publication')
        return self
