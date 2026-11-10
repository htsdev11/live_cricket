# ICC Cricket Django + PostgreSQL + DRF

A standalone Django project. If you already have Django, copy only `cricket/` into your project and add `cricket` and `rest_framework` to `INSTALLED_APPS`; include `cricket.urls` at `/api/`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a PostgreSQL role and database (run as a PostgreSQL admin):

```sql
CREATE USER icc_user WITH PASSWORD 'change-me';
CREATE DATABASE icc_cricket OWNER icc_user;
```

Configuration (`DEBUG`, `ALLOWED_HOSTS`, database name/user/host, `ICC_CLIENT_ID`) is set directly in `config/settings.py`; environment variables and `.env` files are not used. Secrets go in a git-ignored `config/local_settings.py`:

```bash
cp config/local_settings.example.py config/local_settings.py
# then edit SECRET_KEY and DB_PASSWORD
```

Then:

```bash
python manage.py makemigrations cricket
python manage.py migrate
python manage.py test cricket
```

## Populate database

```bash
# Last 7 days completed + next 30 days upcoming + any live matches in window
python manage.py sync_icc

# Just recent results and scorecards
python manage.py sync_icc --mode completed --days-back 7 --days-ahead 0

# Upcoming fixtures without extra scorecard requests
python manage.py sync_icc --mode upcoming --no-details

# Live match updates, preserving each distinct scoreboard state
python manage.py sync_icc --mode live --watch --interval 60 --snapshots

# Fetch only schedules (fast)
python manage.py sync_icc --mode all --no-details
```

`sync_icc` always requests the ICC schedule first and upserts on ICC match ID. For live and completed matches, it optionally retrieves scorecards and upserts innings, batting and bowling. Upcoming scorecards aren't fetched until live/completed. Raw ICC responses are saved to `Match.raw_schedule` and `Match.raw_scorecard`.

## Read-only REST API

Every endpoint requires authentication: a DRF token (`Authorization: Token <key>`) or a logged-in session. Create a token with:

```bash
python manage.py drf_create_token <username>
```

```text
GET /api/matches/
GET /api/matches/?status=completed&format=ODI&team=Pakistan
GET /api/matches/?from_date=2026-11-01&to_date=2026-11-30
GET /api/matches/?q=India&page=2&page_size=25
GET /api/matches/live/
GET /api/matches/upcoming/
GET /api/matches/completed/
GET /api/matches/271651/
GET /api/matches/271651/snapshots/
```

The detail endpoint includes innings, batsmen, bowlers, wickets, partnerships and available notes. `raw_scorecard` is stored in PostgreSQL but is **not** exposed by the API. Filters are case-insensitive (`status`, `format`, `team`); invalid `status` or dates return 400. `?format=` filters by match format, so pick the response type with the `Accept` header instead. The API is read-only and does not trigger ICC requests on user page views. `MatchSnapshot` is optional and deduplicated by score-state fingerprint.

## Project files

- `cricket/models.py`: normalized schema and JSON retention
- `cricket/client.py`: requests-based ICC schedule and scorecard client
- `cricket/importers.py`: database upsert mapping
- `cricket/management/commands/sync_icc.py`: scheduled ingestion entrypoint
- `cricket/serializers.py`, `views.py`, `urls.py`: read-only Django REST endpoints
- `cricket/tests.py`: import idempotency and API smoke tests
- `config/`: example Django settings backed by PostgreSQL

## Production guidance

Run `python manage.py sync_icc --mode live` from cron / Celery Beat, not within a Django HTTP view. Avoid overlapping sync processes. ICC API is third-party, undocumented and may change. The available ICC schedule is scoped by `league_ids=1,9`, date window and max 30 pages, so this is **not guaranteed to include every ICC match**. The saved client ID is a fallback and might stop working. Review ICC access terms, use appropriate polling intervals, and monitor sync failures. Never expose DB credentials, and replace development settings for deployment (DEBUG=0, correct ALLOWED_HOSTS, HTTPS, authenticated management access, rate limiting).
