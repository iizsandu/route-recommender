// frontend/src/App.jsx
import { useState, useEffect } from 'react'
import { useRouteRecommend } from './hooks/useRouteRecommend'
import DisclaimerModal, { hasAcknowledged } from './components/DisclaimerModal'
import RouteForm from './components/RouteForm'
import RouteResults from './components/RouteResults'
import MapView from './components/MapView'
import VoiceAgent from './components/VoiceAgent'

function ShieldIcon() {
  return (
    <svg width="17" height="19" viewBox="0 0 24 27" fill="currentColor">
      <path d="M12 1L3 5v7c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5L12 1z" />
    </svg>
  )
}

function InfoIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  )
}

function AlertIcon() {
  return (
    <svg className="flex-shrink-0 mt-0.5" width="15" height="15" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <circle cx="12" cy="16" r="0.8" fill="currentColor" stroke="none" />
    </svg>
  )
}

export default function App() {
  const [disclaimerOpen, setDisclaimerOpen]               = useState(!hasAcknowledged())
  const [selectedIdx, setSelectedIdx]                     = useState(0)
  const [pinLocations, setPinLocations]                   = useState(null)
  const [personalisedIncidents, setPersonalisedIncidents] = useState(null)

  const { routes, loading, error, recommend } = useRouteRecommend()

  // Auto-pin origin + destination from the route geometry whenever routes arrive.
  // GeoJSON coordinates are [lng, lat] — note index reversal.
  useEffect(() => {
    if (!routes.length) return
    const coords = routes[0].geometry.coordinates
    const first  = coords[0]
    const last   = coords[coords.length - 1]
    setPinLocations({
      origin:      { lng: first[0], lat: first[1] },
      destination: { lng: last[0],  lat: last[1]  },
    })
  }, [routes])

  async function handleFormSubmit(params) {
    setSelectedIdx(0)
    setPersonalisedIncidents(null)
    await recommend(params)
  }

  return (
    <div className="flex flex-col h-screen bg-slate-50">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-5 py-3 bg-white border-b border-slate-200 shadow-sm z-20 sticky top-0">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-9 h-9 bg-indigo-600 rounded-xl text-white flex-shrink-0">
            <ShieldIcon />
          </div>
          <div className="leading-snug">
            <div className="font-semibold text-slate-900 text-sm">Route Recommender</div>
            <div className="text-xs text-slate-400">Safe Navigation Intelligence</div>
          </div>
        </div>

        {/* Right badges + info */}
        <div className="flex items-center gap-2.5">
          <span className="hidden sm:inline-flex items-center text-xs font-medium text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full select-none">
            Delhi NCR
          </span>
          <div className="hidden sm:flex items-center gap-1.5 bg-emerald-50 border border-emerald-200 px-2.5 py-1 rounded-full select-none">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse flex-shrink-0" />
            <span className="text-xs font-medium text-emerald-700">Safety Mode Active</span>
          </div>
          <button
            onClick={() => setDisclaimerOpen(true)}
            className="flex items-center justify-center w-8 h-8 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-all duration-200"
            title="About this app"
            aria-label="Open disclaimer"
          >
            <InfoIcon />
          </button>
        </div>
      </header>

      {/* ── Main layout ─────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden flex-col md:flex-row">

        {/* Sidebar */}
        <aside className="w-full md:w-80 lg:w-[380px] shrink-0 bg-white border-r border-slate-200 overflow-y-auto">
          <div className="p-5 space-y-5">
            <RouteForm
              onSubmit={handleFormSubmit}
              onPinLocations={setPinLocations}
              loading={loading}
            />

            {error && (
              <div className="flex items-start gap-2.5 text-sm text-red-700 bg-red-50 border border-red-200 rounded-xl px-4 py-3">
                <AlertIcon />
                <span>{error}</span>
              </div>
            )}

            <RouteResults
              routes={routes}
              selectedIdx={selectedIdx}
              onSelect={setSelectedIdx}
              personalisedIncidents={personalisedIncidents}
              onPersonalisedIncidents={setPersonalisedIncidents}
            />
          </div>
        </aside>

        {/* Map */}
        <main className="flex-1 min-h-[400px] md:min-h-0 relative">
          <VoiceAgent />
          <MapView
            routes={routes}
            selectedIdx={selectedIdx}
            onSelectRoute={setSelectedIdx}
            pinLocations={pinLocations}
            personalisedIncidents={personalisedIncidents}
          />
        </main>
      </div>

      <DisclaimerModal
        open={disclaimerOpen}
        onClose={() => setDisclaimerOpen(false)}
      />
    </div>
  )
}
