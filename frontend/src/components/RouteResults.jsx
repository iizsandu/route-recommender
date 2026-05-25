// frontend/src/components/RouteResults.jsx
import { useState, useEffect } from 'react'
import { getPersonalisedIncidents } from '../api/client'

const BADGE = {
  Low:    'bg-green-100 text-green-800',
  Medium: 'bg-amber-100 text-amber-800',
  High:   'bg-red-100 text-red-800',
}

// Color dot per crime macro — helps users skim without reading every word
const MACRO_DOT = {
  'Sexual Violence':   'bg-red-600',
  'Kidnapping':        'bg-orange-500',
  'Robbery':           'bg-amber-500',
  'Assault':           'bg-yellow-500',
  'Murder':            'bg-purple-600',
  'Terrorism / Riot':  'bg-red-800',
  'Theft / Burglary':  'bg-gray-500',
  'Drug / Trafficking':'bg-gray-400',
}

function fmtDuration(sec) {
  const m = Math.round(sec / 60)
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)}h ${m % 60}m`
}

function fmtDistance(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`
}

function fmtDate(dateStr) {
  if (!dateStr) return null
  // crime_date comes back as a string like "2025-03-12 00:00:00" or ISO
  const d = new Date(dateStr)
  if (isNaN(d)) return null
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
}

function IncidentCard({ incident }) {
  const dot = MACRO_DOT[incident.crime_macro] ?? 'bg-gray-400'
  const date = fmtDate(incident.crime_date)
  // Truncate summary to 180 chars — enough context without overwhelming the panel
  const summary = incident.summary?.length > 180
    ? incident.summary.slice(0, 180).trimEnd() + '…'
    : incident.summary

  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 p-2.5 space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dot}`} />
        <span className="text-xs font-semibold text-gray-700">{incident.crime_macro}</span>
        {incident.crime_type && incident.crime_type !== incident.crime_macro && (
          <span className="text-xs text-gray-400 truncate">· {incident.crime_type}</span>
        )}
      </div>

      {summary && (
        <p className="text-xs text-gray-600 leading-relaxed">{summary}</p>
      )}

      {incident.victim && (
        <p className="text-xs text-gray-400">Victim: {incident.victim}</p>
      )}
      {incident.weapon_used && (
        <p className="text-xs text-gray-400">Weapon: {incident.weapon_used}</p>
      )}

      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-gray-400 truncate">
          {incident.location_exact
            ? incident.location_exact
            : date}
          {incident.location_exact && date && ` · ${date}`}
        </div>
        {incident.url && (
          <a
            href={incident.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-indigo-500 hover:text-indigo-700 flex-shrink-0"
            onClick={e => e.stopPropagation()}
          >
            Source ↗
          </a>
        )}
      </div>
    </div>
  )
}

const QUESTIONS = [
  {
    key: 'travelling_with',
    label: 'Are you travelling alone?',
    options: ['Alone', 'With one other person', 'In a group'],
  },
  {
    key: 'transport_mode',
    label: 'How are you travelling?',
    options: ['Walking', 'Auto-rickshaw', 'Cab or taxi', 'Own vehicle'],
  },
  {
    key: 'destination_type',
    label: 'What describes your destination?',
    options: ['Busy market or commercial area', 'Residential neighbourhood', 'Office or institution', 'Isolated or poorly lit road'],
  },
]

export default function RouteResults({ routes, selectedIdx, onSelect, personalisedIncidents, onPersonalisedIncidents }) {
  const [showQuestionnaire, setShowQuestionnaire] = useState(false)
  const [answers, setAnswers] = useState({ travelling_with: null, transport_mode: null, destination_type: null })
  const [loadingPersonalised, setLoadingPersonalised] = useState(false)

  // Reset personalised state whenever the user switches to a different route
  useEffect(() => {
    onPersonalisedIncidents(null)
    setShowQuestionnaire(false)
    setAnswers({ travelling_with: null, transport_mode: null, destination_type: null })
  }, [selectedIdx])

  if (!routes.length) return null

  const selectedRoute = routes[selectedIdx]
  const incidents = selectedRoute?.nearby_incidents ?? []
  const allAnswered = answers.travelling_with && answers.transport_mode && answers.destination_type

  async function handleSubmit() {
    const situation = `Woman travelling ${answers.travelling_with} by ${answers.transport_mode} arriving at ${answers.destination_type}`
    // Geometry coordinates are [lng, lat]; endpoint expects [lat, lng]
    const waypoints = selectedRoute.geometry.coordinates.map(([lng, lat]) => [lat, lng])
    setLoadingPersonalised(true)
    setShowQuestionnaire(false)
    const result = await getPersonalisedIncidents(situation, waypoints)
    setLoadingPersonalised(false)
    onPersonalisedIncidents(result)
  }

  // Decide which incident list to show
  const displayIncidents = personalisedIncidents !== null ? personalisedIncidents : incidents
  const isPersonalised = personalisedIncidents !== null

  return (
    <div className="space-y-2 mt-4">
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
        {routes.length} route{routes.length > 1 ? 's' : ''} found
      </p>

      {routes.map((route, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onSelect(i)}
          className={`w-full text-left rounded-xl border p-3 transition-all ${
            selectedIdx === i
              ? 'border-indigo-500 bg-indigo-50 shadow-sm'
              : 'border-gray-200 bg-white hover:border-gray-300'
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-gray-800">
              Route {i + 1}{i === 0 ? ' — Safest' : ''}
            </span>
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${BADGE[route.risk_band]}`}>
              {route.risk_band}
            </span>
          </div>
          <div className="mt-1 flex gap-3 text-xs text-gray-500">
            <span>{fmtDuration(route.duration_sec)}</span>
            <span>·</span>
            <span>{fmtDistance(route.distance_m)}</span>
          </div>
        </button>
      ))}

      {/* Incident drawer */}
      {(displayIncidents.length > 0 || isPersonalised) && (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            {isPersonalised
              ? 'Incidents matching your situation'
              : `${displayIncidents.length} nearby historical incident${displayIncidents.length > 1 ? 's' : ''}`}
          </p>
          {displayIncidents.length === 0 && isPersonalised
            ? <p className="text-xs text-gray-400">No matching incidents found for your situation.</p>
            : displayIncidents.map((incident, i) => <IncidentCard key={i} incident={incident} />)
          }
          {isPersonalised
            ? (
              <button
                onClick={() => onPersonalisedIncidents(null)}
                className="text-xs text-gray-400 hover:text-gray-600 pt-1"
              >
                Reset to general results
              </button>
            )
            : (
              <p className="text-xs text-gray-300 leading-tight pt-1">
                Historically reported incidents from news sources. Not a prediction of future crime.
              </p>
            )
          }
        </div>
      )}

      {/* Personalise button — only when not yet personalised and questionnaire not open */}
      {!isPersonalised && !showQuestionnaire && !loadingPersonalised && (
        <button
          onClick={() => setShowQuestionnaire(true)}
          className="text-xs text-indigo-500 hover:text-indigo-700 mt-1"
        >
          Personalise these results
        </button>
      )}

      {/* Loading state */}
      {loadingPersonalised && (
        <p className="text-xs text-gray-400 mt-1">Finding personalised incidents…</p>
      )}

      {/* Questionnaire */}
      {showQuestionnaire && (
        <div className="mt-2 space-y-3 rounded-xl border border-gray-200 bg-white p-3">
          {QUESTIONS.map(q => (
            <div key={q.key}>
              <p className="text-xs font-medium text-gray-600 mb-1.5">{q.label}</p>
              <div className="flex flex-wrap gap-1">
                {q.options.map(opt => (
                  <button
                    key={opt}
                    onClick={() => setAnswers(prev => ({ ...prev, [q.key]: opt }))}
                    className={`px-2 py-0.5 rounded-full text-xs border transition-all ${
                      answers[q.key] === opt
                        ? 'bg-indigo-600 text-white border-indigo-600'
                        : 'bg-white text-gray-500 border-gray-300 hover:border-gray-400'
                    }`}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <div className="flex items-center gap-3 pt-1">
            <button
              onClick={handleSubmit}
              disabled={!allAnswered}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                allAnswered
                  ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                  : 'bg-gray-200 text-gray-400 cursor-not-allowed'
              }`}
            >
              Find incidents like mine
            </button>
            <button
              onClick={() => setShowQuestionnaire(false)}
              className="text-xs text-gray-400 hover:text-gray-600"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
