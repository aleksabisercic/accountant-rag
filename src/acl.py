"""
JSON-backed access control list for Telegram chat IDs.

Persists to data/acl.json. Seeds from config.yaml on first run.
All mutations save immediately — no restart needed.
"""

import json
import logging
from pathlib import Path

from src.config import CONFIG

logger = logging.getLogger(__name__)

ACL_PATH = Path(__file__).resolve().parent.parent / "data" / "acl.json"

_acl: dict = {
    "admin_chat_ids": [],
    "allowed_chat_ids": [],
    "names": {},
}


def _save() -> None:
    ACL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACL_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_acl, indent=2))
    tmp.rename(ACL_PATH)
    logger.info("ACL saved: %d admins, %d users", len(_acl["admin_chat_ids"]), len(_acl["allowed_chat_ids"]))


def load() -> None:
    """Load ACL from disk, or seed from config.yaml if first run."""
    global _acl
    if ACL_PATH.exists():
        _acl = json.loads(ACL_PATH.read_text())
        _acl.setdefault("names", {})
        logger.info("ACL loaded from %s: %d admins, %d users", ACL_PATH, len(_acl["admin_chat_ids"]), len(_acl["allowed_chat_ids"]))
    else:
        _acl["admin_chat_ids"] = list(CONFIG["telegram"].get("admin_chat_ids", []))
        _acl["allowed_chat_ids"] = list(CONFIG["telegram"].get("allowed_chat_ids", []))
        _save()
        logger.info("ACL seeded from config.yaml and saved to %s", ACL_PATH)


def is_allowed(chat_id: int) -> bool:
    return not _acl["allowed_chat_ids"] or chat_id in _acl["allowed_chat_ids"] or chat_id in _acl["admin_chat_ids"]


def is_admin(chat_id: int) -> bool:
    return chat_id in _acl["admin_chat_ids"]


def add_user(chat_id: int, name: str | None = None) -> str:
    """Add user to allowed list. Returns 'added', 'updated', or 'exists'."""
    already = chat_id in _acl["allowed_chat_ids"]
    if not already:
        _acl["allowed_chat_ids"].append(chat_id)
    if name:
        old_name = _acl["names"].get(str(chat_id))
        _acl["names"][str(chat_id)] = name
        if already and old_name != name:
            _save()
            return "updated"
    if already:
        return "exists"
    _save()
    return "added"


def remove_user(chat_id: int) -> bool:
    """Remove user from allowed list. Returns False if not found."""
    if chat_id not in _acl["allowed_chat_ids"]:
        return False
    _acl["allowed_chat_ids"].remove(chat_id)
    _acl["names"].pop(str(chat_id), None)
    _save()
    return True


def add_admin(chat_id: int, name: str | None = None) -> str:
    """Add admin. Returns 'added', 'updated', or 'exists'."""
    already = chat_id in _acl["admin_chat_ids"]
    if not already:
        _acl["admin_chat_ids"].append(chat_id)
    if name:
        old_name = _acl["names"].get(str(chat_id))
        _acl["names"][str(chat_id)] = name
        if already and old_name != name:
            _save()
            return "updated"
    if already:
        return "exists"
    _save()
    return "added"


def remove_admin(chat_id: int) -> bool:
    """Remove admin. Returns False if not found."""
    if chat_id not in _acl["admin_chat_ids"]:
        return False
    _acl["admin_chat_ids"].remove(chat_id)
    _acl["names"].pop(str(chat_id), None)
    _save()
    return True


def get_name(chat_id: int) -> str | None:
    """Return display name for a chat ID, or None if not set."""
    return _acl["names"].get(str(chat_id))


def get_admins() -> list[int]:
    return list(_acl["admin_chat_ids"])


def get_users() -> list[int]:
    return list(_acl["allowed_chat_ids"])
