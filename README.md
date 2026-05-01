# Store Locator API

Production-ready REST API for multi-location retail store search and management.

## Tech Stack

| Component | Choice | Reason |
|-----------|--------|--------|
| Framework | Flask 3.0 | Required per project spec |
| Database | PostgreSQL + SQLAlchemy | Relational, geospatial-ready |
| Auth | JWT (PyJWT) + bcrypt | Two-token pattern (access 15min / refresh 7d) |
| Distance | Bounding Box + Haversine (geopy) | Required by spec |
| CSV Processing | Python built-in `csv` module | No pandas dependency needed |
| Caching | In-memory (Redis-ready) | Geocoding TTL 30 days |
| Rate Limiting | Flask-Limiter | 100/hour, 10/minute per IP |
| API Docs | Flasgger (Swagger UI) | Auto-generated at `/docs/` |

---

## Local Development Setup

### Prerequisites
- Python 3.10+
- PostgreSQL running locally

### 1. Install dependencies
```bash
cd store_locator
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env — set DATABASE_URL to your local PostgreSQL
```

### 3. Initialize database
```bash
createdb store_locator
python seed.py          # creates tables + seeds 50 stores + creates 3 users
```

### 4. Run
```bash
python run.py           # → http://localhost:5000
# Swagger docs: http://localhost:5000/docs/
# Health check: http://localhost:5000/health
```

### Test credentials (created by seed.py)
| Role | Email | Password |
|------|-------|----------|
| Admin | admin@test.com | AdminTest123! |
| Marketer | marketer@test.com | MarketerTest123! |
| Viewer | viewer@test.com | ViewerTest123! |

---

## Running Tests
```bash
createdb store_locator_test
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/store_locator_test \
  pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## Deployment (Railway — Recommended)

Railway provides free PostgreSQL + Python hosting. Deployment takes about 5 minutes.

### Step 1 — Push code to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
# Create a new GitHub repo, then:
git remote add origin https://github.com/YOUR_USERNAME/store-locator-api.git
git push -u origin main
```

### Step 2 — Create Railway project
1. Go to [railway.app](https://railway.app) → **New Project**
2. Choose **Deploy from GitHub repo** → select your repo
3. Railway auto-detects Python and starts building

### Step 3 — Add PostgreSQL
1. In your Railway project → **+ New** → **Database** → **PostgreSQL**
2. Click the PostgreSQL service → **Variables** tab
3. Copy the `DATABASE_URL` value

### Step 4 — Set environment variables
In your web service → **Variables** tab, add:

| Variable | Value |
|----------|-------|
| `FLASK_ENV` | `production` |
| `SECRET_KEY` | (generate a random 32-char string) |
| `JWT_SECRET_KEY` | (generate a different random 32-char string) |
| `DATABASE_URL` | (paste from PostgreSQL service) |
| `PORT` | `5000` |

Railway also provides `DATABASE_URL` automatically if you link the services.

### Step 5 — Initialize the database
After first deploy, open Railway's shell for your web service and run:
```bash
python init_production.py
```
This creates all tables, seeds 3 users, and imports the 1000 stores.

To import the CSV, upload `stores_1000.csv` to the project root first (or run import via API after deploy).

### Step 6 — Verify
```
GET https://your-app.railway.app/health
→ {"status": "ok", "database": "ok", "service": "store-locator-api"}
```

---

## Deployment (Render — Alternative)

A `render.yaml` is included. Just connect your GitHub repo on [render.com](https://render.com) and it will auto-configure the web service + PostgreSQL database.

After deploy, run the init script via Render's shell:
```bash
python init_production.py
```

---

## API Endpoints

### Public (no auth required)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/stores/search` | Search stores by location |

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | Login → access + refresh tokens |
| POST | `/api/auth/refresh` | Exchange refresh → new access token |
| POST | `/api/auth/logout` | Revoke refresh token |
| GET | `/api/auth/me` | Current user info |

### Store Management (Admin + Marketer)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/stores` | List stores (paginated) |
| POST | `/api/admin/stores` | Create store |
| GET | `/api/admin/stores/{id}` | Get store |
| PATCH | `/api/admin/stores/{id}` | Partial update (name/phone/services/status/hours only) |
| DELETE | `/api/admin/stores/{id}` | Soft deactivate |
| POST | `/api/admin/stores/import` | Batch CSV import (upsert) |

### User Management (Admin only)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/users` | List users |
| POST | `/api/admin/users` | Create user |
| GET | `/api/admin/users/{id}` | Get user |
| PUT | `/api/admin/users/{id}` | Update role/status |
| DELETE | `/api/admin/users/{id}` | Deactivate user |

Full interactive docs at `/docs/` (Swagger UI).

---

## Authentication Flow

```
1. POST /api/auth/login  →  { access_token, refresh_token }
2. All protected requests: Authorization: Bearer <access_token>
3. When access token expires (15 min):
   POST /api/auth/refresh  { refresh_token }  →  { access_token }
4. Logout: POST /api/auth/logout  { refresh_token }
   (refresh token is revoked in DB — cannot be reused)
```

---

## Distance Calculation

Uses the **Bounding Box + Haversine** method required by spec:

1. Calculate a lat/lon bounding box around the search point (SQL pre-filter)
2. `WHERE latitude BETWEEN min_lat AND max_lat AND longitude BETWEEN min_lon AND max_lon`
3. For each candidate store, calculate exact distance using `geopy.distance.geodesic`
4. Filter to stores within `radius_miles`, sort by distance

---

## RBAC Summary

| Permission | Admin | Marketer | Viewer |
|------------|-------|----------|--------|
| Read stores | ✅ | ✅ | ✅ |
| Create/update/delete stores | ✅ | ✅ | ❌ |
| Batch CSV import | ✅ | ✅ | ❌ |
| Manage users | ✅ | ❌ | ❌ |

---

## Project Structure

```
store_locator/
├── run.py                    # Flask entry point
├── config.py                 # Dev/Prod/Test config
├── seed.py                   # Local dev seeding
├── init_production.py        # Production DB init script
├── Procfile                  # Gunicorn start command
├── railway.json              # Railway deployment config
├── render.yaml               # Render deployment config
├── requirements.txt
├── stores_50.csv             # Seed data (50 stores)
├── stores_1000.csv           # Full dataset (1000 stores)
├── app/
│   ├── __init__.py           # App factory
│   ├── models.py             # SQLAlchemy models
│   ├── middleware/auth.py    # JWT + RBAC decorators
│   ├── routes/
│   │   ├── health.py
│   │   ├── auth.py
│   │   ├── stores_public.py
│   │   ├── stores_admin.py
│   │   └── users_admin.py
│   ├── services/
│   │   ├── geo_service.py    # Geocoding + bounding box
│   │   ├── search_service.py # Haversine search
│   │   └── import_service.py # CSV upsert
│   └── utils/validators.py
└── tests/test_api.py
```
