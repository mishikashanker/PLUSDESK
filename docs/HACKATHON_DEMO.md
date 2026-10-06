# A three-minute PulseDesk demo

## What to say

“Daily standups and status chasing eat a distributed team’s morning. PulseDesk
lets people share short updates asynchronously, then creates one daily digest:
what moved forward, what is blocked, and what happens next. Every blocker has a
path back to the original update. Explicit next steps become tasks. The bot only
collects deliberate submissions or messages in an approved standup channel.”

## Before the presentation

1. Start the app with `python run.py` and open `http://127.0.0.1:8000`.
2. Sign in with `admin` / `demo-pulsedesk` in the default demo configuration.
3. Confirm the purple **Local demo** badge. Five made-up updates are loaded.
4. Use a fresh database for a fresh daily snapshot if you need to repeat the
   submission-first presentation: set `DATABASE_URL=sqlite:///./data/rehearsal.db`
   in your local `.env` and restart. Keep the original demo database intact.
5. For a projector, use a desktop browser at roughly 1280px wide. Do a browser
   rehearsal on your laptop; automated visual testing was unavailable here.

## Live walkthrough

| Time | Screen | Demonstration |
| --- | --- | --- |
| 0:00–0:25 | Overview | Explain the problem and the visible local-demo badge. Counts come from saved updates, not fabricated analytics. |
| 0:25–0:55 | Share an update | Select Maya in demo mode. Completed: “Finished usability test notes.” Next: “Share the findings with design.” Blockers: “Waiting on the approved brand assets.” Check the disclosure checkbox and submit. |
| 0:55–1:30 | Daily digest | Generate the digest. Watch it move from queued to ready. Point out Progress, Blockers, and Next steps. |
| 1:30–1:55 | Evidence drawer | Open any blocker’s source. Show the exact original words and stable source ID. Explain that Slack channel updates use real message links; web forms and slash commands use source IDs. |
| 1:55–2:20 | Task board | Show blockers and explicit next steps as local tasks. Move a card to In progress, then Done. These are demonstration workflow stages, not fabricated Jira tickets. |
| 2:20–2:40 | Privacy & integrations | Explain approved-channel filtering, no DMs/history/presence, and no personal productivity scores. |
| 2:40–3:00 | Digest | Export it. Explain that another trigger reuses the same snapshot, and late updates remain visible rather than silently rewriting it. |

## Tell judges what is real

The running FastAPI app, authentication, database persistence, source-evidence
validation, digest state machine, durable task records, local board, scheduler,
and retry/idempotency controls all work in the offline demo.

The offline extractor keeps supplied wording; it is **not** an LLM and does not
claim semantic reasoning. Live mode uses the implemented OpenAI structured-output,
Slack Bolt/Web API, and Jira Cloud REST adapters after credentials and privacy
settings are supplied. No live third-party integration was exercised here.

## Strong answers to likely questions

- **Why is this different from a summarizer?** Every claim carries checked source
  evidence, and actionable findings feed a durable task-tracker workflow.
- **Does it watch everyone?** No. Default mode collects explicit updates only.
  Optional message events are filtered to one configured channel.
- **What if the model invents a blocker?** Unknown IDs and non-verbatim quotes are
  rejected. This checks provenance, not semantic entailment; cautious prompts and
  team review are still needed for real-world rollout.
- **What if the cron runs twice?** A unique team/day digest key returns the same
  saved snapshot. External ambiguous results are parked for review.
- **What if Jira is down?** The outbox retains the task and retries safe failures.
  Unknown create outcomes do not trigger blind duplicate tickets.
- **Does it measure employees?** No activity scraping, ranking, attendance alerts,
  or individual output scores are present.
- **What comes after the hackathon?** Per-user SSO/RBAC, measured retention/erasure,
  provider/project access review, semantic-quality evaluations, production load
  tests, and a controlled live pilot. The edition currently has one shared team
  administrator account.
