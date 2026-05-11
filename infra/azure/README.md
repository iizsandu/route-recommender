# Azure Container Apps — Backend Deployment

The backend runs on Azure Container Apps (Consumption plan, scale-to-zero).
Images are stored in GHCR. Authentication from GitHub Actions to Azure uses
OIDC federation — no long-lived service principal passwords stored anywhere.

## What `container-app.bicep` provisions

| Resource | Details |
|---|---|
| Container App Environment | Consumption plan, shared compute pool |
| Container App | min=0 / max=2 replicas, port 8000, public HTTPS |
| Ingress | TLS terminated by ACA, HTTP rejected |
| Secrets | `cosmos-connection-string`, `ors-api-key` (ACA-managed, never in image) |

Estimated cost: **₹0–50/month** on free-tier grants (180K vCPU-s/month free).

---

## One-time setup (run once per environment)

### Step 1 — Create Azure resource group

```bash
az login
az group create --name route-recommender-rg --location eastasia
```

Pick a region close to your users. `eastasia` (Hong Kong) or `southeastasia`
(Singapore) both have low latency to India with no data residency concerns for
this dataset.

### Step 2 — Deploy Bicep (provisions environment + container app)

The first deploy needs a placeholder image; the GitHub Action will update it.

```bash
# WHY: use the pre-compiled .json, not .bicep directly — az deployment group create
# recompiles Bicep internally using a bundled binary that may diverge from az bicep build.
az deployment group create \
  --resource-group route-recommender-rg \
  --template-file infra/azure/container-app.json \
  --parameters \
      containerImage="mcr.microsoft.com/azuredocs/containerapps-helloworld:latest" \
      cosmosConnectionString="placeholder" \
      orsApiKey="placeholder" \
      allowedOrigins="http://localhost:3000"
```

Note the `containerAppUrl` output — that is your public backend URL.

### Step 3 — Set real secrets on the Container App

Replace placeholder values with real credentials:

```bash
az containerapp secret set \
  --name route-recommender-backend \
  --resource-group route-recommender-rg \
  --secrets \
    cosmos-connection-string="AccountEndpoint=https://..." \
    ors-api-key="eyJvcmc..."
```

### Step 4 — Set up OIDC federation (GitHub Actions → Azure, no password)

**4a. Create an Entra ID app registration:**

```bash
az ad app create --display-name "route-recommender-github-actions"
# Note the appId (client ID) from the output
```

**4b. Create a service principal for the app:**

```bash
az ad sp create --id <appId-from-above>
# Note the objectId from the output
```

**4c. Add a federated credential (links this app to your GitHub repo + branch):**

```bash
az ad app federated-credential create \
  --id <appId> \
  --parameters '{
    "name": "github-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<your-github-username>/route_recommender_web:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

Replace `<your-github-username>` with your actual GitHub username/org.

**4d. Grant the service principal permission to update the Container App:**

```bash
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

az role assignment create \
  --assignee <appId> \
  --role "Contributor" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/route-recommender-rg"
```

`Contributor` on the resource group is sufficient. For tighter scope, use
the Container App's full resource ID instead.

### Step 5 — Add GitHub secrets and variables

In your GitHub repo → Settings → Secrets and variables → Actions:

**Secrets** (encrypted, never shown in logs):

| Secret name | Value |
|---|---|
| `AZURE_CLIENT_ID` | appId from Step 4a |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |

**Variables** (not encrypted, visible in logs — no secrets here):

| Variable name | Value |
|---|---|
| `AZURE_RESOURCE_GROUP` | `route-recommender-rg` |

### Step 6 — Make GHCR package public (simplest for a portfolio project)

After the first workflow run pushes the image, go to:
GitHub → your profile → Packages → `route-recommender-backend` → Package settings → Change visibility → Public

This lets Azure Container Apps pull the image without an `imagePullSecret`.
If you keep the repo private, you need to configure registry credentials on
the Container App separately.

---

## Routine deploys (fully automated after setup)

Every push to `main` that changes `backend/**` or `infra/azure/**` triggers
`.github/workflows/backend-deploy.yml`, which:

1. Builds and pushes `ghcr.io/<owner>/route-recommender-backend:<sha>`
2. Smoke-tests the image locally (hits `/health`)
3. Calls `az containerapp update --image` to deploy the new tag

No human intervention needed.

---

## Rollback

To roll back to any previous commit's image:

```bash
az containerapp update \
  --name route-recommender-backend \
  --resource-group route-recommender-rg \
  --image ghcr.io/<owner>/route-recommender-backend:<previous-sha>
```

Find previous SHAs in the GitHub Actions run history or `git log`.

---

## Verify the deployment

```bash
# Get the public URL
az containerapp show \
  --name route-recommender-backend \
  --resource-group route-recommender-rg \
  --query properties.configuration.ingress.fqdn -o tsv

# Hit the health endpoint (replace with your actual URL)
curl https://<fqdn>/health
# Expected: {"status":"ok"}
```

First request after idle will take 5–10 seconds (cold start). Subsequent
requests are fast.
