"""
JSON-backed invite token + registration store for onboarding.

Persists to data/invites.json. Tokens are single-use with configurable TTL.
Registrations track pending approval requests from unknown users.
All mutations save immediately — no restart needed.
"""

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

INVITES_PATH = Path(__file__).resolve().parent.parent / "data" / "invites.json"

_DEFAULT_TTL_DAYS = 7
_USED_RETENTION_DAYS = 30

_REGISTRATION_TTL_HOURS = 24

_store: dict = {"pending": {}, "used": {}, "registrations": {}}


def _save() -> None:
    """Atomic write: tmp file then rename. Prunes old 'used' entries."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_USED_RETENTION_DAYS)
    _store["used"] = {
        k: v for k, v in _store["used"].items()
        if datetime.fromisoformat(v.get("consumed_at", "2000-01-01T00:00:00+00:00")) > cutoff
    }
    # Prune expired registrations
    reg_cutoff = datetime.now(timezone.utc) - timedelta(hours=_REGISTRATION_TTL_HOURS)
    _store["registrations"] = {
        k: v for k, v in _store["registrations"].items()
        if datetime.fromisoformat(v.get("created_at", "2000-01-01T00:00:00+00:00")) > reg_cutoff
    }
    INVITES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INVITES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_store, indent=2))
    tmp.rename(INVITES_PATH)
    logger.info(
        "Invites saved: %d pending, %d used, %d registrations",
        len(_store["pending"]),
        len(_store["used"]),
        len(_store["registrations"]),
    )


def load() -> None:
    """Load from disk or initialize empty store."""
    global _store
    if INVITES_PATH.exists():
        _store = json.loads(INVITES_PATH.read_text())
        _store.setdefault("pending", {})
        _store.setdefault("used", {})
        _store.setdefault("registrations", {})
        logger.info(
            "Invites loaded from %s: %d pending, %d used, %d registrations",
            INVITES_PATH,
            len(_store["pending"]),
            len(_store["used"]),
            len(_store["registrations"]),
        )
    else:
        _store = {"pending": {}, "used": {}, "registrations": {}}
        _save()
        logger.info("Invites initialized and saved to %s", INVITES_PATH)


def create(label: str, created_by: int, ttl_days: int = _DEFAULT_TTL_DAYS) -> str:
    """Create a new invite token. Returns the token string."""
    token = secrets.token_urlsafe(16)
    now = datetime.now(timezone.utc)
    _store["pending"][token] = {
        "label": label,
        "created_by": created_by,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=ttl_days)).isoformat(),
    }
    _save()
    return token


def consume(token: str, chat_id: int) -> dict | None:
    """Consume a token. Returns invite dict on success, None if invalid/expired/used."""
    invite = _store["pending"].pop(token, None)
    if invite is None:
        return None
    if datetime.now(timezone.utc) > datetime.fromisoformat(invite["expires_at"]):
        _save()
        return None
    _store["used"][token] = {
        "label": invite["label"],
        "created_by": invite["created_by"],
        "consumed_by": chat_id,
        "consumed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save()
    return invite


def list_pending() -> dict[str, dict]:
    """Return all non-expired pending invites."""
    now = datetime.now(timezone.utc)
    return {
        k: v for k, v in _store["pending"].items()
        if datetime.fromisoformat(v["expires_at"]) > now
    }


# ── Registration requests (Flow A fallback) ─────────────────────────


def add_registration(chat_id: int, name: str, company: str) -> bool:
    """Store a pending registration. Returns False if one already exists for this chat_id."""
    key = str(chat_id)
    if key in _store["registrations"]:
        return False
    _store["registrations"][key] = {
        "name": name,
        "company": company,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save()
    return True


def get_registration(chat_id: int) -> dict | None:
    """Return pending registration for chat_id, or None."""
    return _store["registrations"].get(str(chat_id))


def resolve_registration(chat_id: int, approved_by: int | None = None) -> dict | None:
    """Remove and return registration. approved_by=None means rejected."""
    reg = _store["registrations"].pop(str(chat_id), None)
    if reg is None:
        return None
    _save()
    return reg


def remove_registration(chat_id: int) -> bool:
    """Remove a registration (e.g. on re-/start). Returns False if not found."""
    if str(chat_id) not in _store["registrations"]:
        return False
    del _store["registrations"][str(chat_id)]
    _save()
    return True
