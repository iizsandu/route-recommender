# Crime-Aware Route Recommender — Web App

Recommends the safest route between two points in Delhi-NCR for female commuters,
using historical crime data surfaced by the sister extraction repo.

## Architecture

```
Browser (React + MapLibre)
    └── FastAPI backend (Azure Container Apps)
            ├── OpenRouteService API  (candidate routes)
            ├── KDE risk surface      (crime density model)
            └── Azure Cosmos DB       (read-only crime records)
```

## Quick Start (Docker)

```bash
cp .env.example .env
# Fill in COSMOS_CONNECTION_STRING and ORS_API_KEY — even dummy values
# work for P0-1 since the health endpoint doesn't call either service
docker compose up
```

- Backend API:  http://localhost:8000
- Frontend:     http://localhost:3000
- API docs:     http://localhost:8000/docs

## Local Development (without Docker)

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Environment Variables

Copy `.env.example` to `.env` and fill in real values. Every variable is
documented in `.env.example`. The backend will refuse to start if
`COSMOS_CONNECTION_STRING` or `ORS_API_KEY` are missing.

## Deployment

- **Backend:** Azure Container Apps (free tier, scale-to-zero) — see `infra/azure/`
- **Frontend:** Vercel (free tier, auto-deploy on push to `main`) — see `infra/vercel/`
- **CI/CD:** GitHub Actions — see `.github/workflows/`

## Sister Repo

`route_recommender_second` extracts crime records from news articles using an
LLM pipeline and writes structured events to Azure Cosmos DB. This repo is
read-only against that database.
