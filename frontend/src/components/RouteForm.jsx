// frontend/src/components/RouteForm.jsx
import { useState } from 'react'
import TimeOfDayPicker from './TimeOfDayPicker'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export default function RouteForm({ onSubmit, onPinLocations, loading }) {
  const [origin, setOrigin]           = useState('')
  const [destination, setDestination] = useState('')
  const [departTime, setDepartTime]   = useState(new Date().toISOString())
  const [pinning, setPinning]         = useState(false)
  const [pinError, setPinError]       = useState(null)

  function handleSubmit(e) {
    e.preventDefault()
    if (!origin.trim() || !destination.trim()) return
    onSubmit({ origin: origin.trim(), destination: destination.trim(), depart_time: departTime })
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
      <div>
        <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">
          From
        </label>
        <input
          type="text"
          value={origin}
          onChange={(e) => setOrigin(e.target.value)}
          placeholder="e.g. Connaught Place, Delhi"
          required
          className="mt-1 block w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </div>

      <div>
        <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">
          To
        </label>
        <input
          type="text"
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          placeholder="e.g. Lajpat Nagar, Delhi"
          required
          className="mt-1 block w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </div>

      <TimeOfDayPicker value={departTime} onChange={setDepartTime} />

      <button
        type="button"
        onClick={handlePinLocations}
        disabled={pinning || !origin.trim() || !destination.trim()}
        className="w-full bg-white border border-gray-300 hover:border-indigo-400 hover:text-indigo-600 disabled:text-gray-300 disabled:border-gray-200 text-gray-600 text-sm font-medium py-2 rounded-xl transition-colors"
      >
        {pinning ? 'Locating…' : '📍 Show locations on map'}
      </button>

      {pinError && (
        <p className="text-xs text-red-500">{pinError}</p>
      )}

      <button
        type="submit"
        disabled={loading}
        className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white font-medium py-2.5 rounded-xl transition-colors"
      >
        {loading ? 'Finding safest route…' : 'Find safest route'}
      </button>
    </form>
  )
}
