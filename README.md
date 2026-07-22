# URL Shortener

A URL shortener built with **Python, FastAPI, SQLModel, and SQLite/PostgreSQL**.

## Tech Stack

- **FastAPI** — web framework
- **SQLModel** — ORM (SQLAlchemy + Pydantic)
- **SQLite** — default local database (`urls.db`); **PostgreSQL** supported via `DATABASE_URL`
- **Alembic** — schema migrations
- **bcrypt** — password hashing for paywalled links
- **Uvicorn** — ASGI server

## API Endpoints

| Method   | Endpoint                    | Description |
|----------|------------------------------|--------------|
| `POST`   | `/shorten`                   | Body `{"url", "code"?, "expires_at"?, "password"?}` → `{"short_url": "<code>"}`. `X-API-Key` header optional (anonymous if omitted). `code` optional custom code (`409` if taken); `expires_at` optional, must be in the future (`422` otherwise); `password` optional, min 4 chars (`422` otherwise). |
| `POST`   | `/shorten/batch`              | Body `{"urls": [{...shorten fields...}, ...]}` (1–100 items) → `{"results": [{"url", "short_url", "error"}, ...]}`, one entry per input, in order. Requires a valid API key **and** the `enterprise` tier (`401`/`403`). A malformed item `422`s the whole request; a per-item business failure (e.g. taken custom code) only fails that item — the rest still succeed. |
| `GET`    | `/redirect?code={code}&password={password}` | `307` redirect to the original URL. Increments `click_count` and updates `last_accessed_at`. `404` if unknown or deleted, `410` if expired, `401` if the link is password-protected and the password is missing/wrong. No API key required — this endpoint is public. |
| `PUT`    | `/urls/{code}`                | Partial update — body `{"url"?, "expires_at"?, "password"?}`, at least one field required (`422` otherwise). Setting `expires_at` to a past timestamp deactivates the link; sending `expires_at: null` (or a future date) reactivates it. Sending `password: null` clears the paywall. Requires a valid API key; owner-only for owned links (any authenticated user may edit an unowned/anonymous link); `401`/`403`/`404` as appropriate. |
| `DELETE` | `/urls/{code}`                | Soft-deletes the mapping (row is kept, `deleted_at` is stamped — the code can never be reissued). Same auth/ownership rules as `PUT`. `204` on success. |
| `GET`    | `/urls?page={page}&size={size}` | Lists the authenticated caller's own links (paginated, default `page=1&size=10`). Requires a valid API key. Never returns `password_hash`. |
| `GET`    | `/health`                     | `200 {"status": "ok", "database": "ok"}` if the database is reachable, `503 {"status": "error", "database": "unreachable"}` otherwise. No API key required. Used by uptime monitors / orchestration probes, not application traffic. |

## Authentication & Authorization

API keys are checked globally by middleware (`app/middleware/auth.py`), **before** any route's own logic runs — not per-endpoint.

- **Exempt from the key check entirely**: `/health`, `/redirect`, `/shorten` (these must stay reachable without credentials — health checks carry none, redirects are public links, and `/shorten` deliberately supports anonymous use).
- **Every other route** requires a valid `X-API-Key` header:
  - Missing header → `401`
  - Header present but unknown → `401 "Invalid API key"`
  - Valid header → the resolved user is attached to the request and reused by the service layer (no duplicate DB lookup).
- **`/shorten/batch`** has an additional authorization check on top of authentication: the resolved user's `tier` must be `"enterprise"`, or the request gets `403`. New users default to `"hobby"`; upgrading a user is currently a manual DB update (`UPDATE users SET tier = 'enterprise' WHERE email = ...`) — no self-service upgrade endpoint yet.
- **Ownership rules** (delete/edit): a link created with an API key can only be modified/deleted by that same user. A link created **anonymously** (no key) has no owner, so any authenticated user may act on it. Either way, an unknown or already-deleted code returns `404` regardless of auth.

```bash
curl -X POST http://127.0.0.1:8000/shorten \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your-api-key>" \
  -d '{"url": "https://example.com"}'
```

Sample users with their keys are created by `python seed_users.py` (point it at a specific database the same way as `alembic`, via `DATABASE_URL`).

## Password-Protected Links

Any link can be paywalled by setting `password` on creation or via `PUT /urls/{code}`. The password is hashed with **bcrypt** before storage — the plaintext is never persisted or returned by any endpoint.

- `GET /redirect?code=...` on a protected link without `?password=...` → `401 "Password required"`.
- Wrong password → `401 "Invalid password"`. A failed attempt does **not** increment `click_count`.
- Correct password → normal `307` redirect.
- Clearing the paywall: `PUT /urls/{code}` with `{"password": null}`.

## Request Logging

Every request is logged by middleware (`app/middleware/logging.py`) to `logs/request.log` — timestamp, HTTP method, full URL (including query string, unredacted), User-Agent, and client IP. The log path is anchored to the project root regardless of the working directory the server is launched from.

> The full URL is logged as-is, including query parameters — meaning a paywalled link's `?password=...` ends up in the log file in plaintext. This is an accepted trade-off for this learning project; a production system would redact known-sensitive query parameters before logging.

## Click Tracking

Every successful `/redirect` hit atomically increments `click_count` and stamps `last_accessed_at` (single `UPDATE ... RETURNING`, no read-modify-write race). Top 10 most popular codes, ties broken by most recently accessed:

```sql
SELECT short_code, original_url, click_count, last_accessed_at
FROM urls
ORDER BY click_count DESC, last_accessed_at DESC
LIMIT 10;
```

## Project Structure

```
url-shortener/
├── app/
│   ├── main.py             # App entry point, middleware registration
│   ├── routers.py          # HTTP endpoints (thin layer)
│   ├── service.py          # Business logic (code generation, ownership, tracking)
│   ├── repositories.py     # Database queries only
│   ├── models.py           # SQLModel table definitions
│   ├── schemas.py          # Pydantic request/response shapes + validation
│   ├── database.py         # Engine, session, and DB init
│   ├── utils.py            # Short-code generator
│   └── middleware/
│       ├── auth.py         # API key + tier enforcement (runs before routing)
│       └── logging.py      # Access logging
├── migrations/              # Alembic migration environment + versions/
├── tests/                   # pytest suite (url_shortener_test.py)
├── logs/                    # request.log (git-ignored)
├── assets/                  # README images
├── alembic.ini              # Alembic config
├── seed_users.py            # Creates sample users with generated API keys
├── urls.db                  # SQLite database (committed)
├── requirements.txt
└── README.md
```

Layered architecture: **middleware → routers → services → repositories**. Middleware handles cross-cutting concerns (auth, logging) before a request ever reaches a route; each layer below only talks to the one directly beneath it.

## Configuration

The database is chosen by the `DATABASE_URL` environment variable (defaults to `sqlite:///urls.db`):

```bash
DATABASE_URL="postgresql://user:pass@host:5432/dbname?sslmode=require"
```

- **The app** reads `DATABASE_URL` from the shell environment only.
- **Alembic** (`migrations/env.py`) also loads it from a local `.env` file — so plain `alembic` commands target whatever `.env` points at.
- `.env` holds real credentials: it is git-ignored and must never be committed.

## Database Migrations

Schema changes are managed with Alembic. Each database (local SQLite, hosted Postgres) tracks its own applied revisions in an `alembic_version` table.

```bash
# Apply pending migrations
alembic upgrade head                                     # DB from .env (or default SQLite)
DATABASE_URL="postgresql://..." alembic upgrade head     # explicit target

# Check current revision
alembic current

# Create a new migration after editing app/models.py
alembic revision --autogenerate -m "describe the change"
```

Always review an autogenerated migration file before applying it — remove any operations you didn't intend (autogenerate diffs the model against one specific database, and environments can drift), and check whether a foreign key or NOT NULL column needs SQLite batch mode (`op.batch_alter_table`) or a `server_default` to backfill existing rows.

## Prerequisites

- Python 3.10+
- No database installation needed locally — SQLite is built into Python
  (PostgreSQL only if you point `DATABASE_URL` at one)

## Run Locally

```bash
# 1. Clone the repository
git clone <repo-url>
cd url-shortener

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Apply database migrations
alembic upgrade head

# 5. (Optional) Seed sample users with API keys
python seed_users.py

# 6. Start the server
python -m uvicorn app.main:app --reload
```

The API is now running at `http://127.0.0.1:8000`.

Interactive API docs (Swagger UI): **http://127.0.0.1:8000/docs**

## Run Tests

Tests use **pytest** with FastAPI's `TestClient` (no server needs to be running). The suite covers shorten/redirect round-trips, soft deletes and ownership (API key required, owner vs. non-owner vs. anonymous links), click tracking, custom codes, expiry (activation/deactivation via edit), password-protected links, batch shortening (tier-gating, partial-success semantics), paginated listing, the auth/logging middleware, the health check, and validation error cases.

```bash
# From the project root, with the virtual environment activated
python -m pytest -v
```

Tests run against the database the app is configured for (local SQLite by default) and insert rows on each run.

## Test Results

| Endpoint        | p50      | p90      | p95      | p99      |
|-----------------|----------|----------|----------|----------|
| `POST /shorten` | 32.22 ms | 37.64 ms | 37.64 ms | 37.64 ms |
| `GET /redirect` | 22.88 ms | 24.02 ms | 24.02 ms | 24.02 ms |

## Load Testing — Finding the Break Point

### Results — POST /shorten

| Concurrency | p50      | p90       | p95       | p99       | Success % |
|-------------|----------|-----------|-----------|-----------|-----------|
| 10          | 32.2 ms  | 37.6 ms   | 37.6 ms   | 37.6 ms   | 100%      |
| 50          | 18.4 ms  | 82.6 ms   | 150.2 ms  | 270.1 ms  | 100%      |
| 100         | 57.5 ms  | 144.3 ms  | 181.0 ms  | 432.1 ms  | 100%      |
| 200         | 139.5 ms | 200.5 ms  | 256.7 ms  | 454.8 ms  | 100%      |
| 500         | 373.6 ms | 490.2 ms  | 526.5 ms  | 607.1 ms  | **56.8%** ❌ |
| 1000        | 397.5 ms | 530.6 ms  | 584.0 ms  | 735.7 ms  | **56.9%** ❌ |

![Latency vs concurrency — /shorten](assets/latency_shorten.png)

### Results — GET /redirect

| Concurrency | p50      | p90       | p95       | p99       | Success % |
|-------------|----------|-----------|-----------|-----------|-----------|
| 50          | 16.0 ms  | 37.8 ms   | 52.3 ms   | 79.3 ms   | 100%      |
| 100         | 27.7 ms  | 36.0 ms   | 70.8 ms   | 138.9 ms  | 100%      |
| 200         | 56.1 ms  | 65.4 ms   | 144.5 ms  | 297.1 ms  | 100%      |
| 500         | 54.8 ms  | 307.0 ms  | 315.4 ms  | 606.0 ms  | **28.1%** ❌ |
| 1000        | 184.5 ms | 275.0 ms  | 293.6 ms  | 414.1 ms  | **23.3%** ❌ |

![Latency vs concurrency — /redirect](assets/latency_redirect.png)
