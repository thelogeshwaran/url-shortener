# Migration Guide — v3.0.0: Authentication Introduced

This guide covers upgrading an existing integration through the breaking
change recorded in [CHANGELOG.md](CHANGELOG.md) as `v3.0.0` — the
introduction of user accounts and API keys.

## What changed

Before this release, `DELETE /urls/{code}` had no authentication at all —
anyone could delete anyone's short code. As of `v3.0.0`:

- `DELETE /urls/{code}` **requires** a valid `X-API-Key` header.
- Deleting a code you don't own is rejected, even with a valid key.

## Are you affected?

| You call... | Affected? |
|---|---|
| `POST /shorten` without a key | **No** — anonymous shortening still works exactly as before. |
| `POST /shorten` with a key | No change — this was already optional. |
| `GET /redirect` | **No** — this endpoint was never touched. |
| `DELETE /urls/{code}` | **Yes** — this is the endpoint that broke. If you were calling it before, it now fails. |

If you don't call `DELETE`, no action is needed.

## Migration steps

### 1. Get an API key

API keys aren't self-service yet — request one from the app administrator
(the same way the three sample users were originally shared: see
`seed_users.py`). A future release may add self-serve key issuance; until
then, treat this as a manual, one-time setup step per integration.

### 2. Send it on every delete request

Add the `X-API-Key` header to calls that were previously unauthenticated:

**Before (v2.x and earlier):**
```bash
curl -X DELETE https://your-app/urls/abc123
```

**After (v3.0.0+):**
```bash
curl -X DELETE https://your-app/urls/abc123 \
  -H "X-API-Key: <your-api-key>"
```

### 3. Handle two new response codes you couldn't get before

| Status | Meaning | When |
|---|---|---|
| `401` | No key, or the key isn't recognized | Header missing entirely, or the value doesn't match any user |
| `403` | Valid key, wrong owner | The code belongs to a *different* user's key |

Depend on the **status code**, not the exact `detail` message text — the
message wording may vary slightly by code path, but the status code is the
stable part of this contract.

## Things that did *not* change (don't do unnecessary work)

- **Links created before this release have no owner.** They're not locked
  out — any authenticated user (any valid key at all, not a specific one)
  can still delete them. You do not need to "claim" old links or match a
  specific key to a specific old code.
- **`POST /shorten` and `GET /redirect` require no changes whatsoever** —
  neither their request/response shape nor their auth requirements changed.
- **You still don't need a key to shorten URLs anonymously.** Only deletion
  gained a hard requirement.

## FAQ

**I only ever shortened URLs, never deleted one — do I need to do anything?**
No. You are unaffected.

**I get a `403` deleting a code I created before this release.**
That shouldn't happen — pre-existing codes have no owner, so any valid key
can delete them. If you see this, double-check the code actually belongs to
another user (e.g., someone else re-created a code with the same name after
yours was deleted), not a bug in your key.

**Can I keep not sending a key on delete?**
No — this is the one hard requirement in this release. Every `DELETE` call
now needs a valid `X-API-Key` header, with no anonymous fallback.

## A note on how this was actually rolled out

This project shipped the breaking change as an immediate hard cutover —
acceptable here given a small, pre-launch user base the client could reach
directly. For a production API with an unknown number of external
integrators, the more responsible rollout is staged:

1. **Announce** the change and this guide well ahead of the cutover date.
2. **Dual-support window**: accept `DELETE` both with and without a key for
   a grace period, logging which callers are still using the old,
   unauthenticated form.
3. **Warn, don't break**: during the grace period, unauthenticated deletes
   still succeed but return a `Deprecation`/`Sunset` response header
   pointing at this guide.
4. **Cut over** only after usage data shows the old pattern has dropped off
   (or the announced deadline passes) — then, and only then, start
   returning `401` for missing keys.

Skipping straight to a hard cutover (what we did here) is the riskiest option
on that list — it's only reasonable when you can directly reach every caller
beforehand, as the client could in this project's context.
