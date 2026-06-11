// frontend/src/components/RouteForm.jsx
import { useState } from 'react'
import TimeOfDayPicker from './TimeOfDayPicker'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

function MapPinIcon({ size = 14, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      className={className}>
      <path d="M12 2C8.134 2 5 5.134 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.866-3.134-7-7-7z" />
      <circle cx="12" cy="9" r="2.5" />
    </svg>
  )
}

function ShieldIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 27" fill="currentColor">
      <path d="M12 1L3 5v7c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5L12 1z" />
    </svg>
  )
}

function ArrowIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="5" y1="12" x2="19" y2="12" />
      <polyline points="12 5 19 12 12 19" />
    </svg>
  )
}

const PROFILES = [
  {
    id: 'fastest',
    label: 'Fastest',
    description: 'Shortest travel time, no crime weighting',
    color: 'slate',
  },
  {
    id: 'balanced',
    label: 'Balanced',
    description: 'Moderate crime avoidance (~1.7 min extra per high-risk km)',
    color: 'indigo',
  },
  {
    id: 'safest',
    label: 'Safest',
    description: 'Strong crime avoidance (~5 min extra per high-risk km)',
    color: 'emerald',
  },
]

export default function RouteForm({ onSubmit, onPinLocations, loading }) {
  const [origin, setOrigin]           = useState('')
  const [destination, setDestination] = useState('')
  const [departTime, setDepartTime]   = useState(new Date().toISOString())
  const [profile, setProfile]         = useState('balanced')
  const [pinning, setPinning]         = useState(false)
  const [pinError, setPinError]       = useState(null)

  function handleSubmit(e) {
    e.preventDefault()
    if (!origin.trim() || !destination.trim()) return
    onSubmit({
      origin: origin.trim(),
      destination: destination.trim(),
      depart_time: departTime,
      profile,
    })
  }

  async function handlePinLocations() {
    if (!origin.trim() || !destination.trim()) return
    setPinning(true)
    setPinError(null)
    try {
      const [oRes, dRes] = await Promise.all([
        fetch(`${BASE_URL}/geocode?q=${encodeURIComponent(origin.trim())}`),
        fetch(`${BASE_URL}/geocode?q=${encodeURIComponent(destination.trim())}`),
      ])
      if (!oRes.ok) throw new Error(`Could not geocode origin: "${origin}"`)
      if (!dRes.ok) throw new Error(`Could not geocode destination: "${destination}"`)
      const [o, d] = await Promise.all([oRes.json(), dRes.json()])
      onPinLocations({ origin: o, destination: d })
    } catch (err) {
      setPinError(err.message)
    } finally {
      setPinning(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">

      {/* Route origin + destination card */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">

        {/* FROM row */}
        <div className="flex items-center gap-3 px-4 py-3.5">
          <div className="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-full bg-indigo-100">
            <div className="w-3 h-3 rounded-full bg-indigo-600" />
          </div>
          <div className="flex-1 min-w-0">
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-0.5">
              From
            </label>
            <input
              type="text"
              value={origin}
              onChange={e => setOrigin(e.target.value)}
              placeholder="Enter starting location"
              required
              className="block w-full text-sm text-slate-900 placeholder-slate-300 bg-transparent focus:outline-none"
            />
          </div>
        </div>

        {/* Connector divider */}
        <div className="flex items-center px-4">
          <div className="flex flex-col items-center w-8">
            <div className="w-px h-3 bg-slate-200" />
          </div>
          <div className="flex-1 border-t border-slate-100" />
        </div>

        {/* TO row */}
        <div className="flex items-center gap-3 px-4 py-3.5">
          <div className="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-full bg-red-100">
            <svg width="12" height="15" viewBox="0 0 12 16" fill="#ef4444">
              <path d="M6 0C2.686 0 0 2.686 0 6c0 4.5 6 10 6 10s6-5.5 6-10C12 2.686 9.314 0 6 0z" />
              <circle cx="6" cy="6" r="2.5" fill="white" />
            </svg>
          </div>
          <div className="flex-1 min-w-0">
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-0.5">
              To
            </label>
            <input
              type="text"
              value={destination}
              onChange={e => setDestination(e.target.value)}
              placeholder="Enter destination"
              required
              className="block w-full text-sm text-slate-900 placeholder-slate-300 bg-transparent focus:outline-none"
            />
          </div>
        </div>
      </div>

      {/* Departure time card */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm px-4 py-3.5">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2.5">
          Departure time
        </p>
        <TimeOfDayPicker value={departTime} onChange={setDepartTime} />
      </div>

      {/* Route type selector */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm px-4 py-3.5">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2.5">
          Route type
        </p>
        <div className="flex gap-2">
          {PROFILES.map(p => {
            const active = profile === p.id
            const colors = {
              slate:   active ? 'bg-slate-600 text-white border-slate-600'   : 'border-slate-200 text-slate-500 hover:border-slate-400',
              indigo:  active ? 'bg-indigo-600 text-white border-indigo-600' : 'border-slate-200 text-slate-500 hover:border-indigo-400',
              emerald: active ? 'bg-emerald-600 text-white border-emerald-600' : 'border-slate-200 text-slate-500 hover:border-emerald-400',
            }
            return (
              <button
                key={p.id}
                type="button"
                title={p.description}
                onClick={() => setProfile(p.id)}
                className={`flex-1 py-1.5 text-xs font-semibold rounded-lg border transition-all duration-150 ${colors[p.color]}`}
              >
                {p.label}
              </button>
            )
          })}
        </div>
        {/* Description of the currently selected profile */}
        <p className="mt-2 text-xs text-slate-400 leading-snug">
          {PROFILES.find(p => p.id === profile)?.description}
        </p>
      </div>

      {/* Secondary action — pin on map */}
      <button
        type="button"
        onClick={handlePinLocations}
        disabled={pinning || !origin.trim() || !destination.trim()}
        className="w-full flex items-center justify-center gap-2 bg-white border border-slate-200 hover:border-indigo-300 hover:text-indigo-600 hover:shadow-sm disabled:opacity-40 disabled:cursor-not-allowed text-slate-500 text-sm font-medium py-2.5 rounded-xl transition-all duration-200"
      >
        <MapPinIcon />
        {pinning ? 'Locating…' : 'Show locations on map'}
      </button>

      {pinError && (
        <p className="text-xs text-red-500 px-1">{pinError}</p>
      )}

      {/* Primary CTA */}
      <button
        type="submit"
        disabled={loading}
        className="w-full flex items-center justify-center gap-2.5 bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 disabled:bg-indigo-300 disabled:cursor-not-allowed text-white font-semibold text-sm py-3.5 rounded-xl transition-all duration-200 shadow-sm hover:shadow-md"
      >
        {loading ? (
          <>
            <svg className="animate-spin flex-shrink-0" width="15" height="15" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <path d="M21 12a9 9 0 11-6.219-8.56" />
            </svg>
            Finding safest route…
          </>
        ) : (
          <>
            <ShieldIcon />
            Find Safest Route
            <ArrowIcon />
          </>
        )}
      </button>
    </form>
  )
}
