"""ASGI factory. Run migrations before startup; never create schema at import."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
import httpx

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from app.api import router
from app.auth import Auth
from app.seed import seed_demo
from app.worker import build_service, run
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Receive, Scope, Send

from app.bot_handler import create_bot
from app.config import Settings
from app.database import build_engine, session_factory
from app.models import DailyDigest, StandupEntry, User
from app.repository import StandupRepository


class BodyLimit:
    """Limit webhook bytes including chunked input before Bolt parses JSON/form."""

    def __init__(self, app: ASGIApp, max_bytes: int = 32768):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                await JSONResponse({"detail": "Request too large"}, status_code=413)(scope, receive, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break
        iterator = iter(messages)

        async def replay():
            return next(iterator, {"type": "http.request", "body": b"", "more_body": False})

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = build_engine(settings.database_url)
    sessions = session_factory(engine)
    repository = StandupRepository(sessions, settings)
    handler = None
    if settings.slack_bot_token.get_secret_value():
        handler = AsyncSlackRequestHandler(create_bot(settings, repository))

    # Avoid library DEBUG request/payload logging and default exception dumps.
    logging.getLogger("slack_bolt").setLevel(logging.WARNING)
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)

    def verify_schema():
        with engine.connect() as connection:
            for model in (User, StandupEntry, DailyDigest):
                connection.execute(select(model.id).limit(0))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker = None
        try:
            await asyncio.to_thread(verify_schema)
            if settings.mode == 'demo' and settings.seed_demo:
                await asyncio.to_thread(seed_demo, repository)
            async with httpx.AsyncClient() as client:
                api.state.service = build_service(sessions,settings,client)
                if settings.embedded_worker:
                    worker = asyncio.create_task(run(api.state.service))
                yield
        finally:
            if worker:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
            engine.dispose()

    api = FastAPI(title="PulseDesk — Async standups", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    api.add_middleware(BodyLimit)
    api.state.engine = engine
    api.state.sessions = sessions
    api.state.repository = repository
    api.state.settings = settings
    api.state.auth = Auth(settings)
    api.include_router(router)

    @api.exception_handler(SQLAlchemyError)
    async def database_unavailable(request: Request, error: SQLAlchemyError):
        logging.getLogger('pulsedesk').error('database_error type=%s',type(error).__name__)
        return JSONResponse({'detail':'Storage is temporarily unavailable. Retry the same submission.'},status_code=503)

    static_dir = Path(__file__).parent / 'static'
    api.mount('/static', StaticFiles(directory=static_dir), name='static')

    @api.middleware('http')
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api/') else 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @api.get('/')
    async def frontend():
        return FileResponse(static_dir / 'index.html')

    @api.post("/slack/events")
    @api.post("/slack/commands")
    async def webhook(request: Request):
        # Bolt verifies signature + age against the exact unmodified raw bytes.
        # Timestamp only reaches listeners after verification succeeds.
        if handler is None:
            return JSONResponse({"detail":"Slack intake is not configured"},status_code=503)
        try:
            return await handler.handle(request, addition_context_properties={
                "signed_timestamp": request.headers.get("x-slack-request-timestamp", "0"),
            })
        except Exception as exc:
            logging.getLogger("standup").error("webhook_failed error_type=%s", type(exc).__name__)
            return JSONResponse({"detail": "Webhook could not be processed"}, status_code=503)

    @api.get("/health/live")
    async def live():
        return {"status": "ok"}

    @api.get("/health/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return {"status": "ok"}
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    return api
