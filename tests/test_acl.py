import json
from pathlib import Path
from unittest.mock import patch

import pytest

# Patch CONFIG before importing acl
_FAKE_CONFIG = {
    "telegram": {
        "admin_chat_ids": [111],
        "allowed_chat_ids": [222, 333],
    }
}


@pytest.fixture(autouse=True)
def _isolate_acl(tmp_path):
    """Run each test with a fresh ACL file in a temp dir."""
    acl_path = tmp_path / "acl.json"
    with patch("src.acl.CONFIG", _FAKE_CONFIG), patch("src.acl.ACL_PATH", acl_path):
        import src.acl as acl_mod
        # Reset in-memory state fully before each test
        acl_mod._acl = {"admin_chat_ids": [], "allowed_chat_ids": [], "names": {}}
        yield acl_mod, acl_path
        # Clean up in-memory state after test
        acl_mod._acl = {"admin_chat_ids": [], "allowed_chat_ids": [], "names": {}}


def test_seed_from_config(_isolate_acl):
    acl, acl_path = _isolate_acl
    assert not acl_path.exists()
    acl.load()
    assert acl_path.exists()
    data = json.loads(acl_path.read_text())
    assert data["admin_chat_ids"] == [111]
    assert data["allowed_chat_ids"] == [222, 333]


def test_load_existing_file(_isolate_acl):
    acl, acl_path = _isolate_acl
    acl_path.write_text(json.dumps({"admin_chat_ids": [999], "allowed_chat_ids": [888]}))
    acl.load()
    assert acl.is_admin(999)
    assert not acl.is_admin(111)  # config value NOT used


def test_is_allowed_empty_allows_all(_isolate_acl):
    acl, _ = _isolate_acl
    acl._acl = {"admin_chat_ids": [], "allowed_chat_ids": []}
    assert acl.is_allowed(12345)


def test_is_allowed_restricted(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    assert acl.is_allowed(222)
    assert not acl.is_allowed(999)


def test_admin_implicitly_allowed(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    # admin 111 is not in allowed_chat_ids [222, 333] but should still pass
    assert acl.is_allowed(111)


def test_add_user(_isolate_acl):
    acl, acl_path = _isolate_acl
    acl.load()
    assert acl.add_user(444) == "added"
    assert acl.is_allowed(444)
    # Persisted to disk
    data = json.loads(acl_path.read_text())
    assert 444 in data["allowed_chat_ids"]


def test_add_user_with_name(_isolate_acl):
    acl, acl_path = _isolate_acl
    acl.load()
    assert acl.add_user(444, "Test Firma") == "added"
    assert acl.get_name(444) == "Test Firma"
    data = json.loads(acl_path.read_text())
    assert data["names"]["444"] == "Test Firma"


def test_add_user_update_name(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    acl.add_user(444, "Old Name")
    assert acl.add_user(444, "New Name") == "updated"
    assert acl.get_name(444) == "New Name"


def test_add_user_duplicate(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    assert acl.add_user(222) == "exists"  # already in seed


def test_remove_user(_isolate_acl):
    acl, acl_path = _isolate_acl
    acl.load()
    acl.add_user(222, "Some User")  # give it a name first
    assert acl.remove_user(222)
    assert not acl.is_allowed(222)
    assert acl.get_name(222) is None  # name cleaned up
    data = json.loads(acl_path.read_text())
    assert 222 not in data["allowed_chat_ids"]
    assert "222" not in data["names"]


def test_remove_user_not_found(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    assert not acl.remove_user(999)


def test_get_admins_and_users(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    assert acl.get_admins() == [111]
    assert set(acl.get_users()) == {222, 333}


def test_add_admin(_isolate_acl):
    acl, acl_path = _isolate_acl
    acl.load()
    assert acl.add_admin(555, "Admin User") == "added"
    assert acl.is_admin(555)
    assert acl.get_name(555) == "Admin User"
    data = json.loads(acl_path.read_text())
    assert 555 in data["admin_chat_ids"]


def test_add_admin_duplicate(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    assert acl.add_admin(111) == "exists"


def test_remove_admin(_isolate_acl):
    acl, acl_path = _isolate_acl
    acl.load()
    assert acl.remove_admin(111)
    assert not acl.is_admin(111)
    data = json.loads(acl_path.read_text())
    assert 111 not in data["admin_chat_ids"]


def test_remove_admin_not_found(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    assert not acl.remove_admin(999)


def test_remove_admin_cleans_name(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    acl.add_admin(555, "To Remove")
    assert acl.remove_admin(555)
    assert acl.get_name(555) is None


def test_get_name_unknown(_isolate_acl):
    acl, _ = _isolate_acl
    acl.load()
    assert acl.get_name(99999) is None


def test_backward_compat_no_names_key(_isolate_acl):
    """Old acl.json without 'names' key should load fine."""
    acl, acl_path = _isolate_acl
    acl_path.write_text(json.dumps({"admin_chat_ids": [111], "allowed_chat_ids": [222]}))
    acl.load()
    assert acl.is_admin(111)
    assert acl.get_name(111) is None  # no crash, just None
