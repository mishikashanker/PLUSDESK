"""Single-team dashboard auth. Signed HttpOnly sessions and explicit CSRF."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
from app.config import Settings


class Auth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.key = (settings.session_secret.get_secret_value() or secrets.token_hex(32)).encode()
        self.salt = secrets.token_bytes(16)
        self.password_hash = hashlib.pbkdf2_hmac('sha256', settings.dashboard_password.get_secret_value().encode(), self.salt, 200000)
        self.attempts: dict[str, list[float]] = defaultdict(list)

    def authenticate(self, request: Request, username: str, password: str) -> dict:
        self.check_origin(request)
        ip = request.client.host if request.client else 'unknown'
        now = time.time()
        self.attempts[ip] = [x for x in self.attempts[ip] if now-x < 60]
        if len(self.attempts[ip]) >= 8:
            raise HTTPException(429, 'Too many attempts. Try again in one minute.')
        self.attempts[ip].append(now)
        if len(self.attempts) > 2048:
            self.attempts = defaultdict(list, {k:v for k,v in self.attempts.items() if v and now-v[-1] < 60})
        given = hashlib.pbkdf2_hmac('sha256', password.encode(), self.salt, 200000)
        if not hmac.compare_digest(username, self.settings.dashboard_user) or not hmac.compare_digest(given, self.password_hash):
            raise HTTPException(401, 'Username or password is incorrect.')
        self.attempts[ip] = []
        return {'user': username, 'csrf': secrets.token_urlsafe(24), 'expires': int(now)+28800}

    def encode(self, payload: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
        return body+'.'+hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest()

    def require(self, request: Request) -> dict:
        try:
            body, sig = request.cookies.get('pulse_session', '').rsplit('.', 1)
            if not hmac.compare_digest(sig, hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest()):
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
            if payload['expires'] <= time.time() or payload['user'] != self.settings.dashboard_user:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(401, 'Sign in to access your team workspace.') from None
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            self.check_origin(request)
            if not hmac.compare_digest(request.headers.get('x-pulse-csrf', ''), payload['csrf']):
                raise HTTPException(403, 'Session security check failed. Refresh and try again.')
        return payload

    @staticmethod
    def check_origin(request: Request):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, 'Cross-origin requests are not allowed.')
