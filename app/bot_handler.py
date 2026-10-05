"""Slack adapter: verify via Bolt, scope before persistence, no history reads."""

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from slack_bolt.async_app import AsyncApp
from slack_bolt.authorization import AuthorizeResult

from app.config import Settings
from app.contracts import Intake, Submission
from app.repository import IntakeRejected


def create_bot(settings: Settings, intake: Intake) -> AsyncApp:
    """Injected intake keeps persistence independent of Slack's payload shape."""

    async def authorize(enterprise_id, team_id, user_id):
        # This is intentionally a single-workspace installation, not OAuth SaaS.
        if team_id != settings.slack_team_id:
            return None
        return AuthorizeResult(
            enterprise_id=enterprise_id, team_id=team_id,
            bot_token=settings.slack_bot_token.get_secret_value(),
            bot_user_id=settings.slack_bot_user_id,
        )

    bot = AsyncApp(
        signing_secret=settings.slack_signing_secret.get_secret_value(),
        authorize=authorize, process_before_response=True,
        request_verification_enabled=True,
        raise_error_for_unhandled_request=False,
    )

    async def save(item: Submission):
        # DB-only work; no LLM, tracker, or Slack network call on the ACK path.
        # A timed-out thread may still commit. Retry deduplication handles that.
        return await asyncio.wait_for(asyncio.to_thread(intake.accept, item), timeout=2.0)

    @bot.command("/standup")
    async def command(ack, body: dict[str, Any], context):
        if (body.get("team_id"), body.get("channel_id")) != (
            settings.slack_team_id, settings.standup_channel_id,
        ):
            await ack(response_type="ephemeral", text="Use /standup in the designated standup channel.")
            return
        trigger = body.get("trigger_id", "")
        if not trigger or not body.get("user_id"):
            await ack(response_type="ephemeral", text="Submission identity is missing.")
            return
        # A slash command has no original channel message timestamp/permalink.
        # Save its stable source ID; never fabricate a message URL or persist
        # response_url (a credential-bearing temporary webhook).
        item = Submission(
            platform="slack", workspace_id=body["team_id"], channel_id=body["channel_id"],
            external_user_id=body["user_id"], input_kind="command",
            source_id="command:" + hashlib.sha256(trigger.encode()).hexdigest(),
            source_url=None, raw_text=body.get("text", ""),
            submitted_at=datetime.fromtimestamp(int(context["signed_timestamp"]), timezone.utc),
        )
        try:
            receipt = await save(item)
        except IntakeRejected as exc:
            await ack(response_type="ephemeral", text=str(exc))
            return
        await ack(response_type="ephemeral", text=(
            f"{'Already saved' if receipt.duplicate else 'Saved'} update {receipt.entry_id}. "
            "This update will be included in the team digest and may create a task ticket."
        ))

    @bot.event("message")
    async def message(ack, body: dict[str, Any], event: dict[str, Any]):
        # Do not store ignored events, user profiles, metadata, or rejected text.
        if (not settings.collect_channel_messages
                or body.get("team_id") != settings.slack_team_id
                or event.get("channel") != settings.standup_channel_id
                or event.get("subtype") is not None
                or event.get("bot_id") is not None
                or event.get("hidden")
                or event.get("user") == settings.slack_bot_user_id):
            await ack()
            return
        timestamp = event.get("ts", "")
        if not re.fullmatch(r"\d{10,12}\.\d{6}", timestamp) or not event.get("user"):
            await ack()
            return
        # Trusted configured host + validated platform timestamp, not model output.
        url = (f"https://{settings.slack_workspace_domain}.slack.com/archives/"
               f"{settings.standup_channel_id}/p{timestamp.replace('.', '')}")
        item = Submission(
            platform="slack", workspace_id=body["team_id"], channel_id=event["channel"],
            external_user_id=event["user"], input_kind="message",
            source_id=f"message:{event['channel']}:{timestamp}", source_url=url,
            raw_text=event.get("text", ""),
            submitted_at=datetime.fromtimestamp(float(timestamp), timezone.utc),
        )
        try:
            await save(item)
        except IntakeRejected:
            # Malformed input is not transient; acknowledge without storing it.
            pass
        await ack()

    @bot.error
    async def failure(error, body, logger, response):
        # Do not log exception messages or payloads: SQL exceptions include data.
        logger.error("standup_intake_failed error_type=%s", type(error).__name__)
        response.status = 503
        response.body = "Temporary intake failure. Retry the same request."

    return bot
