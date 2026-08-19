import uuid

from conftest import _get_test_user, _shorten, client


def test_list_urls_requires_api_key():
    response = client.get("/urls")
    assert response.status_code == 401


def test_list_urls_returns_only_own_urls():
    owner = _get_test_user("list-owner@test.com")
    other = _get_test_user("list-other@test.com")

    owner_url = f"https://example.com/{uuid.uuid4()}"
    other_url = f"https://example.com/{uuid.uuid4()}"
    _shorten(owner_url, owner.api_key)
    _shorten(other_url, other.api_key)

    # size=1000: this test user accumulates URLs across every run of this
    # suite (a shared, never-reset urls.db), so the default page size can't
    # be trusted to include the URL just created above.
    response = client.get("/urls?size=1000", headers={"X-API-Key": owner.api_key})
    assert response.status_code == 200

    body = response.json()
    returned_urls = [item["original_url"] for item in body["urls"]]
    assert owner_url in returned_urls
    assert other_url not in returned_urls


def test_list_urls_does_not_expose_password_hash():
    user = _get_test_user("list-pw@test.com")
    client.post(
        "/shorten",
        json={"url": f"https://example.com/{uuid.uuid4()}", "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )

    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200
    for item in response.json()["urls"]:
        assert "password_hash" not in item
        assert "password" not in item


def test_list_urls_pagination_respects_size():
    user = _get_test_user("list-paginate@test.com")
    for _ in range(5):
        _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    response = client.get("/urls?page=1&size=2", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200
    body = response.json()
    assert len(body["urls"]) == 2
    assert body["page"] == 1
    assert body["size"] == 2
    assert body["total"] >= 5


def test_list_urls_second_page_returns_different_items():
    user = _get_test_user("list-page2@test.com")
    codes = [_shorten(f"https://example.com/{uuid.uuid4()}", user.api_key) for _ in range(5)]

    page1 = client.get("/urls?page=1&size=2", headers={"X-API-Key": user.api_key}).json()
    page2 = client.get("/urls?page=2&size=2", headers={"X-API-Key": user.api_key}).json()

    page1_codes = {item["short_code"] for item in page1["urls"]}
    page2_codes = {item["short_code"] for item in page2["urls"]}
    assert page1_codes.isdisjoint(page2_codes)


def test_list_urls_total_reflects_full_count_not_page_size():
    user = _get_test_user("list-total@test.com")
    for _ in range(3):
        _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    response = client.get("/urls?page=1&size=1", headers={"X-API-Key": user.api_key})
    body = response.json()
    assert len(body["urls"]) == 1
    assert body["total"] >= 3


def test_list_urls_empty_for_user_with_no_links():
    user = _get_test_user("list-empty@test.com")
    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200
    body = response.json()
    assert body["urls"] == []
    assert body["total"] == 0
