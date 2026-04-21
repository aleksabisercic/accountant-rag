import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_invites(tmp_path):
    """Run each test with a fresh invites file in a temp dir."""
    invites_path = tmp_path / "invites.json"
    with patch("src.invites.INVITES_PATH", invites_path):
        import src.invites as inv

        inv._store = {"pending": {}, "used": {}, "registrations": {}}
        yield inv, invites_path
        inv._store = {"pending": {}, "used": {}, "registrations": {}}


def test_create_returns_token(_isolate_invites):
    inv, _ = _isolate_invites
    token = inv.create("ACME d.o.o.", created_by=111)
    assert isinstance(token, str)
    assert len(token) > 10
    assert token in inv._store["pending"]


def test_create_persists_to_disk(_isolate_invites):
    inv, path = _isolate_invites
    token = inv.create("ACME d.o.o.", created_by=111)
    data = json.loads(path.read_text())
    assert token in data["pending"]
    assert data["pending"][token]["label"] == "ACME d.o.o."
    assert data["pending"][token]["created_by"] == 111


def test_consume_valid(_isolate_invites):
    inv, _ = _isolate_invites
    token = inv.create("Test Firma", created_by=111)
    result = inv.consume(token, chat_id=999)
    assert result is not None
    assert result["label"] == "Test Firma"
    assert result["created_by"] == 111


def test_consume_moves_to_used(_isolate_invites):
    inv, path = _isolate_invites
    token = inv.create("Test Firma", created_by=111)
    inv.consume(token, chat_id=999)
    assert token not in inv._store["pending"]
    assert token in inv._store["used"]
    assert inv._store["used"][token]["consumed_by"] == 999
    # Persisted
    data = json.loads(path.read_text())
    assert token in data["used"]


def test_consume_invalid_token(_isolate_invites):
    inv, _ = _isolate_invites
    assert inv.consume("nonexistent_token", chat_id=999) is None


def test_consume_expired(_isolate_invites):
    inv, _ = _isolate_invites
    token = inv.create("Expired Co", created_by=111, ttl_days=0)
    # Manually set expires_at to the past
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    inv._store["pending"][token]["expires_at"] = past
    assert inv.consume(token, chat_id=999) is None
    # Token removed from pending even though expired
    assert token not in inv._store["pending"]


def test_double_consume(_isolate_invites):
    inv, _ = _isolate_invites
    token = inv.create("Double Co", created_by=111)
    assert inv.consume(token, chat_id=999) is not None
    assert inv.consume(token, chat_id=888) is None


def test_load_empty_creates_file(_isolate_invites):
    inv, path = _isolate_invites
    assert not path.exists()
    inv.load()
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["pending"] == {}
    assert data["used"] == {}
    assert data["registrations"] == {}


def test_load_existing(_isolate_invites):
    inv, path = _isolate_invites
    store = {
        "pending": {"tok1": {"label": "A", "created_by": 1, "created_at": "x", "expires_at": "x"}},
        "used": {},
    }
    path.write_text(json.dumps(store))
    inv.load()
    assert "tok1" in inv._store["pending"]
    assert inv._store["pending"]["tok1"]["label"] == "A"


def test_list_pending_excludes_expired(_isolate_invites):
    inv, _ = _isolate_invites
    tok_valid = inv.create("Valid Co", created_by=111, ttl_days=7)
    tok_expired = inv.create("Expired Co", created_by=111, ttl_days=0)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    inv._store["pending"][tok_expired]["expires_at"] = past

    pending = inv.list_pending()
    assert tok_valid in pending
    assert tok_expired not in pending


def test_used_pruning(_isolate_invites):
    inv, _ = _isolate_invites
    # Manually insert an old used entry
    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    inv._store["used"]["old_token"] = {
        "label": "Old",
        "created_by": 1,
        "consumed_by": 2,
        "consumed_at": old_date,
    }
    # Insert a recent used entry
    inv._store["used"]["new_token"] = {
        "label": "New",
        "created_by": 1,
        "consumed_by": 3,
        "consumed_at": datetime.now(timezone.utc).isoformat(),
    }
    inv._save()
    assert "old_token" not in inv._store["used"]
    assert "new_token" in inv._store["used"]


def test_backward_compat_missing_keys(_isolate_invites):
    """Old invites.json without 'used' or 'registrations' key should load fine."""
    inv, path = _isolate_invites
    path.write_text(json.dumps({"pending": {}}))
    inv.load()
    assert inv._store["used"] == {}
    assert inv._store["registrations"] == {}


# ── Registration tests ───────────────────────────────────────────────


def test_add_registration(_isolate_invites):
    inv, path = _isolate_invites
    assert inv.add_registration(999, "Marko Marković", "ACME d.o.o.")
    reg = inv.get_registration(999)
    assert reg is not None
    assert reg["name"] == "Marko Marković"
    assert reg["company"] == "ACME d.o.o."
    # Persisted
    data = json.loads(path.read_text())
    assert "999" in data["registrations"]


def test_add_registration_duplicate_blocked(_isolate_invites):
    inv, _ = _isolate_invites
    assert inv.add_registration(999, "Marko", "ACME")
    assert not inv.add_registration(999, "Petar", "Other")  # rejected
    assert inv.get_registration(999)["name"] == "Marko"  # unchanged


def test_get_registration_not_found(_isolate_invites):
    inv, _ = _isolate_invites
    assert inv.get_registration(999) is None


def test_resolve_registration(_isolate_invites):
    inv, _ = _isolate_invites
    inv.add_registration(999, "Marko", "ACME")
    reg = inv.resolve_registration(999, approved_by=111)
    assert reg is not None
    assert reg["name"] == "Marko"
    # Removed from store
    assert inv.get_registration(999) is None


def test_resolve_registration_not_found(_isolate_invites):
    inv, _ = _isolate_invites
    assert inv.resolve_registration(999) is None


def test_remove_registration(_isolate_invites):
    inv, _ = _isolate_invites
    inv.add_registration(999, "Marko", "ACME")
    assert inv.remove_registration(999)
    assert inv.get_registration(999) is None


def test_remove_registration_not_found(_isolate_invites):
    inv, _ = _isolate_invites
    assert not inv.remove_registration(999)


def test_registration_pruned_after_ttl(_isolate_invites):
    inv, _ = _isolate_invites
    inv.add_registration(999, "Marko", "ACME")
    # Backdate to >24h ago
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    inv._store["registrations"]["999"]["created_at"] = old
    inv._save()  # triggers pruning
    assert inv.get_registration(999) is None
