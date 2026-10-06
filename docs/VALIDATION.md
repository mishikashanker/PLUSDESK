# Validation record

## Completed checks

- **45 Python tests passed**, covering signed Slack commands/events, bad signatures
  and stale timestamps, workspace/channel/DM/bot filters, concurrent source
  deduplication, UTC/local date rollover, scoped database integrity, digest-day
  uniqueness, authenticated reads, CSRF/origin checks, consent, idempotent demo
  seeding, the complete digest/source/task/export flow, late snapshot exclusion,
  fabricated evidence rejection, held ambiguous remote operations, Jira ADF
  create/update calls, and repeated exact findings reusing mapped tickets. Added
  checks cover distinct-source repeat counts, occurrence history, request retry
  safety, explicit deadline set/change/clear and overdue states, sync conflicts,
  source-scoped foreign keys, and Jira due-date payloads.
- **Frontend DOM smoke flow passed** against a running FastAPI service using
  happy-dom: sign-in, dashboard rendering, update submission, search, evidence
  drawer, digest generation, task sync, board movement, repeat badge, three-source
  occurrence drawer, saved/reopened deadline editor, and privacy disclosures.
  This is functional DOM testing; it does not validate browser layout or pixels.
- JavaScript syntax check passed (`node --check app/static/app.js`).
- Alembic metadata check passed: no missing upgrade operations.
- Fresh SQLite migration upgrade, downgrade, and re-upgrade passed.
- Populated `product_001` database upgrade retained task IDs, backfilled only
  retained source evidence, passed foreign-key checks and matched current metadata.
- PostgreSQL DDL compiled successfully from the product models.
- Local FastAPI startup and health endpoint passed.

Tests used Python 3.12. The source targets Python 3.11+ and the container uses
Python 3.11; a Python 3.11 execution run was not performed here.

## Reproduce

```bash
pytest -q
alembic check
node --check app/static/app.js
```

For the optional synthetic UI flow, install Node 22+ and run:

```bash
npm install
python tests/run_frontend_smoke.py
```

Node is only needed for this additional test, not for running the product.

## Not claimed

Full browser rendering/screenshot checks could not be completed: the cloud
browser blocked localhost and the local Chromium download was unavailable.
Responsive desktop/mobile styles, focus trapping, labelled controls and escaping
are implemented; inspect them in your own browser before the presentation.

No real Slack message, OpenAI call, Jira issue, PostgreSQL server, Docker build,
or public-domain deployment was exercised. Jira was verified with mocked HTTP
responses. Credentials and a live pilot are needed for those checks.

One upstream Starlette/httpx test-client deprecation warning remains; it did not
fail the tests. Production security/load assessments, retention/erasure and
per-user access controls remain rollout work, as documented in LIVE_SETUP.md.
