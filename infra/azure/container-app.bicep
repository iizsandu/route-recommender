// infra/azure/container-app.bicep

@description('Azure region — defaults to the resource group\'s own region')
param location string = resourceGroup().location

@description('Shared compute environment that hosts one or more Container Apps')
param containerAppEnvName string = 'route-recommender-env'

@description('Name of the Container App (the actual running service)')
param containerAppName string = 'route-recommender-backend'

@description('Full image ref with tag, e.g. ghcr.io/owner/repo:abc1234')
param containerImage string

@description('Cosmos DB connection string — never hard-code, always injected at deploy time')
@secure()
param cosmosConnectionString string

@description('OpenRouteService API key')
@secure()
param orsApiKey string

@description('Comma-separated CORS origins, e.g. https://myapp.vercel.app')
param allowedOrigins string = 'http://localhost:3000'

// WHY: managedEnvironments is the shared network + compute plane.
// All Container Apps in the same environment can talk to each other
// over internal DNS without going through the public internet.
resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppEnvName
  location: location
  properties: {
    // WHY: empty properties block = Consumption plan by default.
    // Consumption = scale-to-zero, billed per vCPU-second used.
    // Free grants: 180K vCPU-seconds + 360K GiB-seconds per month.
    // Do NOT set workloadProfiles here — that switches to Dedicated plan (paid).
  }
}


resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id  // WHY: binds this app to our environment

    configuration: {
      ingress: {
        external: true        // WHY: false = internal-only (no public URL); we need public
        targetPort: 8000      // WHY: must match uvicorn's --port 8000 in the Dockerfile CMD
        allowInsecure: false  // WHY: ACA terminates TLS for us; reject plain HTTP
        transport: 'auto'     // WHY: negotiates HTTP/1.1 or HTTP/2 based on client capability
      }

      secrets: [
        {
          // WHY: ACA secret names must be lowercase-with-hyphens (not SCREAMING_SNAKE)
          name: 'cosmos-connection-string'
          value: cosmosConnectionString  // the @secure() param from chunk 1
        }
        {
          name: 'ors-api-key'
          value: orsApiKey
        }
      ]
    }

    template: {
      containers: [
        {
          name: 'backend'
          image: containerImage  // injected at deploy time by the GitHub Action
          resources: {
            cpu: json('0.25')  // WHY: json() needed — Bicep treats bare 0.25 as ambiguous type
            memory: '0.5Gi'    // WHY: exactly 2× CPU in GiB; Azure rejects any other ratio
          }
          env: [
            {
              name: 'COSMOS_CONNECTION_STRING'
              secretRef: 'cosmos-connection-string'  // WHY: secretRef, not value — never expose secret in plain env
            }
            {
              name: 'ORS_API_KEY'
              secretRef: 'ors-api-key'
            }
            {
              name: 'ALLOWED_ORIGINS'
              value: allowedOrigins
            }
            {
              name: 'LOG_FORMAT'
              value: 'json'  // WHY: JSON logs in production for structured ingestion; console only in local dev
            }
          ]
          probes: [
            {
              type: 'Readiness'  // WHY: Readiness = ACA waits for 200 before routing any traffic
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5   // WHY: give uvicorn time to bind before first probe
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0  // WHY: scale-to-zero = ₹0 when idle; cold start ~5-10s is acceptable
        maxReplicas: 2  // WHY: cap at 2; free grants cover ~180K vCPU-s/month comfortably
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '10'  // WHY: spin up a second replica when >10 concurrent requests
              }
            }
          }
        ]
      }
    }
  }
}

output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
