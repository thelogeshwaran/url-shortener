# Changelog

All notable changes to the URL Shortener API are documented here, in
[Keep a Changelog](https://keepachangelog.com/) style, using
[Semantic Versioning](https://semver.org/) (`major.minor.patch`):

- **major** — incompatible change; forces existing clients to update their code.
- **minor** — new functionality, backward-compatible.
- **patch** — backward-compatible bug fix or internal-only change.

Dates are omitted — this changelog was reconstructed retroactively from the
project's build history rather than tracked release-by-release.

---

## [4.5.1] - patch

### Fixed / Internal
- Access logging (method, URL, User-Agent, IP) written to `logs/request.log` via middleware.
- Per-middleware execution-time breakdown logged internally to `logs/timing.log`.
- New Relic APM instrumentation for request tracing.

No client-visible change — pure observability/operations tooling.

## [4.5.0] - minor

### Added
- Blacklist middleware: specific API keys can now be blocked (`403`) via an
  externally-editable config file, checked on every request.
- `X-Execution-Time` response header reporting request latency.

### Changed (internal)
- API-key and tier checks consolidated from per-route dependencies into global
  middleware, preserving the existing auth contract (same routes exempt, same
  401/403 semantics).

Purely additive from a client's perspective: a new header appears, and a new
opt-in blocking capability exists, but no existing key or request is affected
unless an operator explicitly blacklists it.

## [4.4.0] - minor
### Added
- `GET /health` — reports server + database connectivity (`200`/`503`). No auth required. **(M2A2 Q16, bonus)**

## [4.3.0] - minor
### Added
- `GET /urls?page=&size=` — paginated listing of the authenticated caller's own links. **(M2A2 Q15)**

## [4.2.0] - minor
### Added
- Optional `password` field on create (`POST /shorten`) and edit (`PUT /urls/{code}`).
- `GET /redirect` accepts an optional `password` parameter; returns `401` if required and missing/wrong. **(M2A2 Q14)**

No existing (passwordless) link changes behavior — the password check only ever triggers for newly created, opted-in protected links.

## [4.1.0] - minor
### Added
- `PUT /urls/{code}` — partial edit of `url`, `expires_at`, `password`. Setting
  `expires_at` into the past deactivates a link; clearing/extending it
  reactivates one. **(M2A2 Q13)**

New endpoint; `DELETE /urls/{code}` and all other routes are unaffected.

## [4.0.0] - **major** ⚠️
### Changed — breaking
- `POST /shorten/batch` now requires the caller's account to be on the
  `enterprise` tier (`403` otherwise). Previously any authenticated user could
  batch-create. **(M2A2 Q12)**

Existing `hobby`-tier callers of the batch endpoint lose access they
previously had — this is an authorization-rule change on an already-shipped
endpoint, explicitly called out as breaking by this project's own rubric.

## [3.3.0] - minor
### Added
- `POST /shorten/batch` — shorten 1–100 URLs in one request; partial success
  per item, malformed items 422 the whole request. **(M2A2 Q10)**

New endpoint; does not alter `POST /shorten`.

## [3.2.0] - minor
### Added
- Optional `code` field on `POST /shorten` for custom short codes; `409` if
  already taken. **(M2A2 Q9)**

## [3.1.0] - minor
### Added
- Optional `expires_at` field on `POST /shorten`; expired links return `410`
  instead of redirecting. **(M2A2 Q8)**

No existing link could have an expiry before this feature existed, so no
existing link's behavior changes.

## [3.0.0] - **major** ⚠️
### Added
- `users` table, `X-API-Key` header support, ownership on short codes.
- `DELETE /urls/{code}` becomes a soft delete. **(M2A2 Q5)**

### Changed — breaking
- `DELETE /urls/{code}` now **requires** a valid API key (`401` without one)
  and enforces ownership (`403` if owned by another user). Previously this
  endpoint had no authentication at all.

`POST /shorten`'s API key support is optional (anonymous use still works —
non-breaking on its own), but the mandatory-auth requirement now enforced on
delete is an authentication-rule change on a previously-open endpoint —
breaking by this project's own rubric, so the release as a whole is major.

## [2.0.0] - **major** ⚠️
### Changed — breaking
- `POST /shorten` no longer returns the same code for a previously-shortened
  URL — every call now creates a new code. **(M2A2 Q4)**

This reverses a guarantee the API previously made on this exact endpoint with
an unchanged request/response shape — callers relying on that idempotency
(e.g., re-submitting a URL to retrieve its existing code) now silently get
different behavior with no error. A behavioral break, not a format break, but
a break nonetheless.

## [1.1.0] - minor
### Added
- `click_count` and `last_accessed_at` tracked per code, incremented
  atomically on every successful redirect. **(M2A2 Q3)**

## [1.0.1] - patch
### Fixed
- Empty/malformed `url` in `POST /shorten` now returns `422` instead of
  crashing or silently accepting it. **(M2A2 Q2)**

## [1.0.0] - initial release
### Added
- `POST /shorten` — shorten a URL; duplicate submissions of the same URL
  return the same code (per the original spec).
- `GET /redirect?code=` — redirect to the original URL; `404` for unknown codes.
