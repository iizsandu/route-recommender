// frontend/src/components/RouteForm.jsx
import { useState } from 'react'
import TimeOfDayPicker from './TimeOfDayPicker'
import LocationInput from './LocationInput'

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

export default function RouteForm({ onSubmit, onPinLocations, loading }) {
  const [origin, setOrigin]           = useState('')
  const [destination, setDestination] = useState('')
  // WHY separate *Place state: when set, it holds the lat/lng of a popular
  // place picked from the LocationInput dropdown, so submit/pin can skip
  // geocoding for that field entirely. Cleared on any manual text edit.
  const [originPlace, setOriginPlace]           = useState(null)
  const [destinationPlace, setDestinationPlace] = useState(null)
  const [departTime, setDepartTime]   = useState(new Date().toISOString())
  const [pinning, setPinning]         = useState(false)
  const [pinError, setPinError]       = useState(null)

  function handleOriginChange(text, place) {
    setOrigin(text)
    setOriginPlace(place)
  }

  function handleDestinationChange(text, place) {
    setDestination(text)
    setDestinationPlace(place)
  }

  function handleSubmit(e) {
    e.preventDefault()
    if (!origin.trim() || !destination.trim()) return
    onSubmit({
      origin: originPlace ? { lat: originPlace.lat, lng: originPlace.lng } : origin.trim(),
      destination: destinationPlace ? { lat: destinationPlace.lat, lng: destinationPlace.lng } : destination.trim(),
      depart_time: departTime,
    })
  }

  async function geocodeOrUsePlace(text, place, label) {
    if (place) return { lat: place.lat, lng: place.lng }
    const res = await fetch(`${BASE_URL}/geocode?q=${encodeURIComponent(text)}`)
    if (!res.ok) throw new Error(`Could not geocode ${label}: "${text}"`)
    return res.json()
  }

  async function handlePinLocations() {
    if (!origin.trim() || !destination.trim()) return
    setPinning(true)
    setPinError(null)
    try {
      const [o, d] = await Promise.all([
        geocodeOrUsePlace(origin.trim(), originPlace, 'origin'),
        geocodeOrUsePlace(destination.trim(), destinationPlace, 'destination'),
      ])
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
      {/* WHY no overflow-hidden: would clip the LocationInput suggestion dropdown */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">

        {/* FROM row */}
        <div className="flex items-center gap-3 px-4 py-3.5">
          <div className="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-full bg-indigo-100">
            <div className="w-3 h-3 rounded-full bg-indigo-600" />
          </div>
          <div className="flex-1 min-w-0">
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-0.5">
              From
            </label>
            <LocationInput
              value={origin}
              onChange={handleOriginChange}
              placeholder="Enter starting location"
              inputClassName="block w-full text-sm text-slate-900 placeholder-slate-300 bg-transparent focus:outline-none"
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
            <LocationInput
              value={destination}
              onChange={handleDestinationChange}
              placeholder="Enter destination"
              inputClassName="block w-full text-sm text-slate-900 placeholder-slate-300 bg-transparent focus:outline-none"
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
