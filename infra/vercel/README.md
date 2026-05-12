# Frontend Deployment — Vercel

Vercel auto-deploys the `frontend/` directory on every push to `master`.
These are the one-time manual setup steps.

---

## One-time setup

### 1. Import the GitHub repo into Vercel

1. Go to [vercel.com/new](https://vercel.com/new) and sign in with GitHub.
2. Select the `route_recommender_web` repository.
3. Vercel auto-detects Vite. Confirm these settings:
   - **Framework Preset:** Vite
   - **Root Directory:** `frontend`
   - **Build Command:** `npm run build`
   - **Output Directory:** `dist`
4. Click **Deploy** — this first deploy will succeed (App.jsx has no hard dependency on the API URL at build time beyond the placeholder).

### 2. Set environment variables in Vercel

In the Vercel dashboard: **Project Settings → Environment Variables**

| Variable | Scope | Value |
|---|---|---|
| `VITE_API_BASE_URL` | Production | Your Azure Container Apps URL, e.g. `https://route-recommender-backend.azurecontainerapps.io` |
| `VITE_API_BASE_URL` | Preview | Same as Production (or a staging URL if you have one) |
| `VITE_MAPTILER_KEY` | Production + Preview | Your MapTiler API key |

> **Why separate scopes?** Vercel creates a unique preview URL for every PR.
> Setting the var in both scopes means PR previews also have a working backend URL.

### 3. Update CORS on the backend

Add the Vercel production URL to `ALLOWED_ORIGINS` in the backend's environment variables:

```
ALLOWED_ORIGINS=http://localhost:3000,https://your-project.vercel.app
```

If you use Vercel preview deployments (per-PR URLs), those are subdomains of `vercel.app`
but with unique names — CORS is handled properly in P0-5 with a wildcard pattern for
preview URLs.

### 4. Trigger a redeploy

After setting env vars, click **Redeploy** (without clearing the build cache) in the
Vercel dashboard. The `VITE_API_BASE_URL` will now be baked into the bundle.

---

## How auto-deploy works

```
git push master
    → GitHub notifies Vercel webhook
    → Vercel pulls the repo, runs npm ci + npm run build in frontend/
    → On success: swaps the live deployment atomically (old build stays up during build)
    → On failure: deployment cancelled, previous build stays live
```

The GitHub Actions `frontend-ci.yml` runs in parallel as a PR check —
it catches build failures before merge, reducing bad deploys reaching Vercel.

---

## Checking it works

```bash
# 1. Visit the production URL — should render "Route Recommender — Delhi NCR"
# 2. The API badge should show "online" once the backend is deployed (P0-3)
# 3. Open browser DevTools → Network — confirm /health call returns 200
```

If the badge shows "offline":
- Check `VITE_API_BASE_URL` is set in Vercel and points to the correct backend URL
- Check the backend's `ALLOWED_ORIGINS` includes the Vercel domain
- Check the backend is actually running (`curl <backend-url>/health`)
