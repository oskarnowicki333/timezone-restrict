"""
timezone-restrict — small FastAPI gate service.

POST /timezone with body:
    { "timezone": "Asia/Tokyo", "fullUrl": "https://..." }

If `timezone` is in the configured allow-list -> respond with SUCCESS_STATUS + RESPONSE_TEXT.
Otherwise -> respond with ERROR_STATUS + ERROR_TEXT.

Everything (allowed zones, texts, statuses, rate limit) is configured via env vars,
so you can change behaviour in DigitalOcean App Platform without redeploying code.
"""

import logging
import os

try:
    # Optional: load a local .env during development. No-op if not installed
    # or no .env present (production uses real env vars from App Platform).
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("timezone-restrict")


# --- Config (read once at startup from env) ---------------------------------

def _parse_allowed(raw: str) -> set[str]:
    """Comma-separated list -> set of trimmed zone names (case preserved)."""
    return {z.strip() for z in raw.split(",") if z.strip()}


ALLOWED_TIMEZONES = _parse_allowed(os.getenv("ALLOWED_TIMEZONES", "Asia/Tokyo"))
RESPONSE_TEXT = os.getenv("RESPONSE_TEXT", "OK")
SUCCESS_STATUS = int(os.getenv("SUCCESS_STATUS", "200"))
ERROR_TEXT = os.getenv("ERROR_TEXT", "Forbidden")
ERROR_STATUS = int(os.getenv("ERROR_STATUS", "403"))
RATE_LIMIT = os.getenv("RATE_LIMIT", "60/minute")

logger.info("Allowed timezones: %s", sorted(ALLOWED_TIMEZONES))
logger.info("Rate limit: %s", RATE_LIMIT)


# --- Rate limiting (anti spam / DDoS) ---------------------------------------

def client_key(request: Request) -> str:
    """
    App Platform sits behind a proxy, so request.client.host is the proxy IP.
    Use the first IP from X-Forwarded-For when present, else fall back.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=client_key)

app = FastAPI(title="timezone-restrict")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --- Request model ----------------------------------------------------------

class TimezoneRequest(BaseModel):
    timezone: str
    fullUrl: str


# --- Endpoints --------------------------------------------------------------

@app.post("/timezone")
@limiter.limit(RATE_LIMIT)
async def check_timezone(request: Request, body: TimezoneRequest) -> PlainTextResponse:
    allowed = body.timezone in ALLOWED_TIMEZONES
    logger.info("timezone=%s allowed=%s fullUrl=%s", body.timezone, allowed, body.fullUrl)

    if allowed:
        return PlainTextResponse(content=RESPONSE_TEXT, status_code=SUCCESS_STATUS)
    return PlainTextResponse(content=ERROR_TEXT, status_code=ERROR_STATUS)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
