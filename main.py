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
from fastapi.middleware.cors import CORSMiddleware
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


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to default on empty/invalid values
    (so a blank var in the App Platform panel can't crash the container)."""
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw)
    except ValueError:
        logger.warning("Env %s=%r is not an int; using default %s", name, raw, default)
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw if raw not in (None, "") else default


ALLOWED_TIMEZONES = _parse_allowed(_env_str("ALLOWED_TIMEZONES", "Asia/Tokyo"))
RESPONSE_TEXT = _env_str("RESPONSE_TEXT", "OK")
SUCCESS_STATUS = _env_int("SUCCESS_STATUS", 200)
ERROR_TEXT = _env_str("ERROR_TEXT", "Forbidden")
ERROR_STATUS = _env_int("ERROR_STATUS", 403)
RATE_LIMIT = _env_str("RATE_LIMIT", "60/minute")

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

# Allow requests from any origin (browser frontends on other domains).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
