import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone
from urllib.parse import urlencode

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.config import Settings
from app.contracts import Submission
from app.main import create_app
from app.models import DailyDigest, DigestSource, StandupEntry, User
from app.repository import IntakeRejected


@pytest.fixture
def configured(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    settings = Settings(
        _env_file=None, database_url=url,
        slack_signing_secret="test-secret", slack_bot_token="xoxb-test",
        slack_team_id="T123", slack_bot_user_id="U999", seed_demo=False, embedded_worker=False,
        standup_channel_id="C123", slack_workspace_domain="test-team",
    )
    return settings


@pytest.fixture
def api(configured):
    app = create_app(configured)
    with TestClient(app) as client:
        yield app, client


def signed(client, body, path="/slack/commands", timestamp=None, secret="test-secret", **headers):
    ts = str(timestamp if timestamp is not None else int(time.time()))
    raw = json.dumps(body) if isinstance(body, dict) and path == "/slack/events" else urlencode(body)
    digest = hmac.new(secret.encode(), f"v0:{ts}:{raw}".encode(), hashlib.sha256).hexdigest()
    return client.post(path, content=raw, headers={
        "content-type": "application/json" if path == "/slack/events" else "application/x-www-form-urlencoded",
        "x-slack-request-timestamp": ts, "x-slack-signature": "v0=" + digest, **headers,
    })


def command_payload(**overrides):
    return dict(team_id="T123", channel_id="C123", user_id="U123", command="/standup",
                text="Shipped tests. Blocked on staging access.", trigger_id="unique-trigger", **overrides)


def counts(app):
    with app.state.sessions() as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in (User, StandupEntry))


def test_command_commit_before_ack_and_retry(api):
    app, client = api
    payload = command_payload(response_url="https://hooks.slack.com/SECRET")
    first = signed(client, payload)
    assert first.status_code == 200
    assert "Saved update" in first.json()["text"]
    assert counts(app) == (1, 1)
    second = signed(client, payload, **{"x-slack-retry-num": "1"})
    assert "Already saved" in second.json()["text"]
    assert counts(app) == (1, 1)
    with app.state.sessions() as session:
        entry = session.scalar(select(StandupEntry))
        assert entry.source_url is None
        assert entry.source_id.startswith("command:")
        assert "SECRET" not in entry.raw_text
        assert entry.submitted_at.tzinfo is timezone.utc


@pytest.mark.parametrize("change", [
    {"channel_id": "COTHER"}, {"team_id": "TOTHER"}, {"text": "  "}, {"text": "a" * 4001},
])
def test_scope_and_validation_reject_without_storage(api, change):
    app, client = api
    payload = command_payload()
    payload.update(change)
    signed(client, payload)
    assert counts(app) == (0, 0)


@pytest.mark.parametrize("kwargs", [{"secret": "wrong"}, {"timestamp": 1}])
def test_bad_signature_and_replay_rejected(api, kwargs):
    app, client = api
    assert signed(client, command_payload(), **kwargs).status_code == 401
    assert counts(app) == (0, 0)


def event_payload(**event_changes):
    event = dict(type="message", channel="C123", user="U123", text="Waiting on API access", ts="1791300000.000001")
    event.update(event_changes)
    return dict(type="event_callback", team_id="T123", event_id="Ev123", event=event)


def test_channel_collection_disabled_by_default(api):
    app, client = api
    assert signed(client, event_payload(), "/slack/events").status_code == 200
    assert counts(app) == (0, 0)


def test_channel_message_link_and_dedup(configured):
    app = create_app(configured.model_copy(update={"collect_channel_messages": True}))
    with TestClient(app) as client:
        for event_id in ("Ev1", "Ev2"):
            payload = event_payload()
            payload["event_id"] = event_id
            assert signed(client, payload, "/slack/events").status_code == 200
        assert counts(app) == (1, 1)
        with app.state.sessions() as session:
            entry = session.scalar(select(StandupEntry))
            assert entry.source_url == "https://test-team.slack.com/archives/C123/p1791300000000001"


@pytest.mark.parametrize("changes", [
    {"channel": "COTHER"}, {"channel": "D123"}, {"bot_id": "B123"},
    {"subtype": "message_changed"}, {"subtype": "message_deleted"}, {"user": "U999"},
    {"ts": "invalid"}, {"text": ""},
])
def test_channel_privacy_filters(configured, changes):
    app = create_app(configured.model_copy(update={"collect_channel_messages": True}))
    with TestClient(app) as client:
        assert signed(client, event_payload(**changes), "/slack/events").status_code == 200
        assert counts(app) == (0, 0)


def submission(**overrides):
    return replace(Submission(
        platform="slack", workspace_id="T123", channel_id="C123", external_user_id="U123",
        input_kind="command", source_id="command:stable", source_url=None, raw_text="Shipped tests",
        submitted_at=datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc),
    ), **overrides)


def test_concurrent_dedup_and_local_day(api):
    app, _ = api
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: app.state.repository.accept(submission()), range(8)))
    assert len({r.entry_id for r in receipts}) == 1
    assert sum(not r.duplicate for r in receipts) == 1
    assert counts(app) == (1, 1)
    with app.state.sessions() as session:
        assert session.scalar(select(StandupEntry)).standup_date == date(2026, 10, 7)


def test_repository_scope_defense(api):
    app, _ = api
    with pytest.raises(IntakeRejected):
        app.state.repository.accept(submission(workspace_id="TOTHER"))
    assert counts(app) == (0, 0)


def test_database_failure_is_retryable_and_does_not_leak_text(api, monkeypatch, caplog):
    app, client = api
    def fail(_item):
        raise OperationalError("INSERT", {"private": "DO-NOT-LOG"}, Exception("secret"))
    monkeypatch.setattr(app.state.repository, "accept", fail)
    response = signed(client, command_payload())
    assert response.status_code == 503
    assert "DO-NOT-LOG" not in caplog.text
    assert "secret" not in response.text


def test_signed_url_verification_and_body_limit(api):
    app, client = api
    result = signed(client, {"type": "url_verification", "challenge": "hello"}, "/slack/events")
    assert result.status_code == 200 and result.json()["challenge"] == "hello"
    assert client.post("/slack/events", content=b"x" * 32769).status_code == 413
    assert counts(app) == (0, 0)


def digest(**overrides):
    return dict(platform="slack", workspace_id="T123", channel_id="C123",
                digest_date=date(2026, 10, 7), timezone_name="Asia/Kolkata",
                cutoff_at=datetime(2026, 10, 7, tzinfo=timezone.utc), **overrides)


def test_digest_day_uniqueness(api):
    app, _ = api
    with app.state.sessions.begin() as session:
        session.add(DailyDigest(**digest()))
    with pytest.raises(IntegrityError), app.state.sessions.begin() as session:
        session.add(DailyDigest(**digest()))


def test_cross_channel_digest_source_fk(api):
    app, _ = api
    receipt = app.state.repository.accept(submission())
    params = digest()
    params["channel_id"] = "COTHER"
    with app.state.sessions.begin() as session:
        row = DailyDigest(**params)
        session.add(row)
        session.flush()
        digest_id = row.id
    with pytest.raises(IntegrityError), app.state.sessions.begin() as session:
        session.add(DigestSource(digest_id=digest_id, entry_id=receipt.entry_id,
                                platform="slack", workspace_id="T123", channel_id="C123"))


def test_no_public_update_listing(api):
    _, client = api
    assert client.get("/standups").status_code == 404
    assert client.get("/health/ready").status_code == 200
