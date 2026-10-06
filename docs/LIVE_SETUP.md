# Switching from demo to a live pilot

Run the offline demo first. Use a separate database for real data. `.env` is a
secret file and must not be committed. These adapters are implemented but have
not been tested against live accounts in this environment.

## 1. Authentication and deployment

- Set `MODE=live`, a unique `DASHBOARD_PASSWORD` of at least 12 characters, a
  random `SESSION_SECRET` of at least 32 characters, and `SECURE_COOKIES=true`.
- Keep the same session secret across API restarts and replicas.
- Serve the app over HTTPS with the included Caddy configuration and your domain.
- Use PostgreSQL for concurrent deployments:
  `DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DBNAME`.
- Set `SEED_DEMO=false`, `EMBEDDED_WORKER=false`. Run a separate `python -m
  app.worker` process. Compose already separates API, migrations, and worker.
- This version is a **single team, shared administrator** application. Do not
  advertise per-user enterprise SSO or RBAC. Web updates in live mode use the
  authenticated dashboard username; Slack updates carry Slack user IDs.

## 2. OpenAI

Set `OPENAI_API_KEY` and a structured-output-compatible `OPENAI_MODEL` (default
`gpt-4o-mini`). Set `LLM_DISCLOSURE_CONFIRMED=true` only after communicating the
provider and data-disclosure policy to participants. The request contains scoped
update IDs and text. It requests a strict JSON schema, then checks exact quoted
source evidence locally. Invalid or refused output is not published. There is no
silent offline fallback in live mode.

## 3. Jira Cloud

Configure `JIRA_BASE_URL=https://YOUR_TEAM.atlassian.net`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`, and `JIRA_ISSUE_TYPE` (default Task).

Use a dedicated bot account with Browse Projects, Create Issues, and Edit Issues
for the chosen project. Your Jira issue type may require additional fields; add
those to `JiraTracker` before the pilot if your project requires them. The adapter
sends Atlassian Document Format descriptions containing evidence, source IDs/URLs,
and a stable PulseDesk label.

Confirm the project audience matches the standup audience before setting
`TRACKER_AUDIENCE_CONFIRMED=true`. The setting is an operator declaration, not an
API-based permission audit. Jira integration uses API-token basic auth, suitable
for a limited bot pilot; a distributable enterprise app should use OAuth 2.0.

Exact repeated quoted findings (ignoring case/whitespace) reuse their mapped issue
and send a PUT update. Different wording remains a distinct finding; semantic
cross-day issue matching is outside this edition. The board associates a reused
task with its latest digest; earlier digest text and source membership remain
immutable. In-flight/ambiguous records are not overwritten. Live ticket workflow
stages are managed in Jira, not through the demo board controls.

If creation times out or its result is uncertain, sync status becomes `review`.
Search Jira for the label `pulsedesk-<task UUID>`. Verify whether an issue exists
before an operator repairs its mapping/state. Do not simply clear a review state
and retry creation. Automated ambiguous-outcome reconciliation is a future step.

## 4. Slack

Create a Slack app and a `/standup` command with Request URL:
`https://YOUR_DOMAIN/slack/commands`. Grant bot scope `commands`. Install it and
configure `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_TEAM_ID`,
`SLACK_BOT_USER_ID`, `STANDUP_CHANNEL_ID`, and `SLACK_WORKSPACE_DOMAIN`.

Default to `COLLECT_CHANNEL_MESSAGES=false`; keep event subscriptions off. Slash
commands and the web form have source IDs, not invented message permalinks.

For optional public-channel intake, add `message.channels` and its required
`channels:history` scope, enable Events API at `https://YOUR_DOMAIN/slack/events`,
set `COLLECT_CHANNEL_MESSAGES=true`, and invite the bot only to the approved
channel. For a private channel use `message.groups` / `groups:history`. Slack can
send events from every channel where the bot is present, so minimize membership;
server filters do not narrow the platform subscription itself.

No `conversations.history` polling is used. DMs, unrelated channels, bot/self
messages, edits/deletes, hidden events, and invalid input are rejected/ignored.
Threads inside the designated channel are allowed in message mode. Put a visible
notice in the channel describing the mode, digest audience, LLM, Jira project,
cutoff timezone, and retention policy.

To publish daily digests, add `chat:write`, invite the app to the output channel,
and set `PUBLISH_TO_SLACK=true`. The worker posts only to `STANDUP_CHANNEL_ID`.
Otherwise the live digest is saved locally. Normal retries reuse its saved row.
A crash or timeout after remote publication may leave an ambiguous result; it is
held for operator review rather than silently reposted. `client_msg_id` is sent
as a stable identifier but is not relied on as an exactly-once API guarantee.

## 5. Schedule and a controlled pilot

Set `DIGEST_HOUR`, `DIGEST_MINUTE`, and `TEAM_TIMEZONE`. The worker checks every
two seconds; generation is due at or after the configured local time. It does
not backfill missed historical days automatically. A manual trigger can generate
an earlier date. Empty days are skipped. The first snapshot freezes the included
updates; late updates are visible in Updates and counted as excluded in Digest.

Test one made-up update in the live environment, confirm the exact source link,
OpenAI schema output, Jira ticket, and Slack digest, then trigger it again and
verify the IDs do not change. Review provider errors and project-specific fields.

Before real employee use: implement/test retention and erasure across source
copies, digests, Jira tickets, and backups; add per-user SSO/RBAC; audit deployment
logging and secrets; test concurrency/ACK timing and recovery; and document the
operational review process. This is a hackathon-ready product and a pilot
foundation, not a certified enterprise deployment.

## Official references

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Slack request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
- [Slack slash commands](https://docs.slack.dev/interactivity/implementing-slash-commands/)
- [Jira REST API v3](https://developer.atlassian.com/cloud/jira/platform/rest/v3/)
- [Jira API-token authentication](https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/)
