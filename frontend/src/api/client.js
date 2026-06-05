// frontend/src/api/client.js
import axios from 'axios'

// WHY fallback to '/api': in local dev VITE_API_BASE_URL is unset.
// Vite's proxy rewrites /api/* → http://localhost:8000/* so requests
// appear same-origin and avoid CORS preflights during development.
const baseURL = import.meta.env.VITE_API_BASE_URL || '/api'

const client = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 60_000,
})

export default client

export async function getPersonalisedIncidents(situation, waypoints, radiusKm = 2.0, maxTotal = 8) {
  try {
    const res = await client.post('/routes/incidents/personalised', {
      situation,
      waypoints,
      radius_km: radiusKm,
      max_total: maxTotal,
    })
    return res.data
  } catch {
    return []
  }
}
