import json
import os
from contextlib import contextmanager
from pathlib import Path

from conftest import _get_test_user, client

_BLACKLIST_PATH = Path(__file__).resolve().parent.parent / "blacklist.json"


@contextmanager
def _with_blacklisted_keys(keys):
    """Temporarily overwrite blacklist.json, restoring the original after."""
    original = _BLACKLIST_PATH.read_text()
    _BLACKLIST_PATH.write_text(json.dumps({"blocked_keys": keys}))
    try:
        yield
    finally:
        _BLACKLIST_PATH.write_text(original)


def test_blacklisted_key_is_blocked():
    user = _get_test_user("blacklist-blocked@test.com")
    with _with_blacklisted_keys([user.api_key]):
        response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 403


def test_non_blacklisted_key_still_works():
    user = _get_test_user("blacklist-allowed@test.com")
    with _with_blacklisted_keys(["some-other-key-not-this-one"]):
        response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200


def test_blacklist_takes_effect_without_restart():
    """The file is read fresh on every request — no in-memory caching."""
    user = _get_test_user("blacklist-live-reload@test.com")

    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200

    with _with_blacklisted_keys([user.api_key]):
        response = client.get("/urls", headers={"X-API-Key": user.api_key})
        assert response.status_code == 403

    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200


def test_blacklisting_a_never_registered_key_still_gets_rejected():
    """
    The blacklist now runs before auth, so a blacklisted key is rejected
    (403) without ever reaching the auth DB lookup — even for a key that
    was never a real registered user. This is the efficiency benefit
    blacklisting is meant to provide: reject cheaply, before paying for
    identity resolution.
    """
    fake_key = "totally-fake-never-registered-key"
    with _with_blacklisted_keys([fake_key]):
        response = client.get("/urls", headers={"X-API-Key": fake_key})
    assert response.status_code == 403


def test_blacklist_file_found_regardless_of_working_directory():
    """
    The blacklist file path must not depend on the process's current
    working directory — same bug class already fixed for request.log.
    """
    user = _get_test_user("blacklist-cwd@test.com")
    original_cwd = os.getcwd()
    os.chdir("/tmp")
    try:
        with _with_blacklisted_keys([user.api_key]):
            response = client.get("/urls", headers={"X-API-Key": user.api_key})
    finally:
        os.chdir(original_cwd)
    assert response.status_code == 403
