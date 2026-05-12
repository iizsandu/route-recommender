import { useState, useEffect } from 'react'

// Phase 2: replace with <MapView /> + <RouteForm /> layout

function useApiHealth() {
  const [status, setStatus] = useState('checking')

  useEffect(() => {
    const apiUrl = import.meta.env.VITE_API_BASE_URL
    // WHY: in local dev VITE_API_BASE_URL is unset; fall back to /api/health
    // which vite.config.js proxies to http://localhost:8000/health.
    // In production the full URL is baked in at build time.
    const healthUrl = apiUrl ? `${apiUrl}/health` : '/api/health'

    fetch(healthUrl)
      .then(r => (r.ok ? setStatus('online') : setStatus('offline')))
      .catch(() => setStatus('offline'))
  }, [])

  return status
}

const BADGE_COLOR = {
  checking: 'bg-gray-400',
  online: 'bg-green-500',
  offline: 'bg-red-500',
}

export default function App() {
  const apiStatus = useApiHealth()

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="text-center space-y-4">
        <h1 className="text-2xl font-semibold text-gray-800">
          Route Recommender — Delhi NCR
        </h1>
        <div className="flex items-center justify-center gap-2 text-sm text-gray-600">
          <span className={`inline-block w-2 h-2 rounded-full ${BADGE_COLOR[apiStatus]}`} />
          <span>API: {apiStatus}</span>
        </div>
      </div>
    </div>
  )
}
