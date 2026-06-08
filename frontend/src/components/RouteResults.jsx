// frontend/src/components/RouteResults.jsx
import { useState, useEffect } from 'react'
import { getPersonalisedIncidents } from '../api/client'

// Risk band badge styles
const BADGE = {
  Low:    { bg: 'bg-emerald-100', text: 'text-emerald-800', dot: 'bg-emerald-500' },
  Medium: { bg: 'bg-amber-100',   text: 'text-amber-800',   dot: 'bg-amber-500'   },
  High:   { bg: 'bg-red-100',     text: 'text-red-800',     dot: 'bg-red-500'     },
}

// Route type metadata by index
const ROUTE_META = [
  { icon: '🛡', label: 'Safest Route',   insight: 'Lowest historical crime exposure'        },
  { icon: '⚖', label: 'Balanced Route',  insight: 'Best compromise between safety and time' },
  { icon: '⚡', label: 'Fastest Route',   insight: 'Shortest travel time available'           },
]

// Left accent bar color per crime macro
const SEVERITY_COLOR = {
  'Sexual Violence':    '#dc2626',
  'Kidnapping':         '#ea580c',
  'Robbery':            '#d97706',
  'Assault':            '#ca8a04',
  'Murder':             '#7c3aed',
  'Terrorism / Riot':   '#991b1b',
  'Theft / Burglary':   '#6b7280',
  'Drug / Trafficking': '#9ca3af',
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
  const d = new Date(dateStr)
  if (isNaN(d)) return null
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
}

function ClockIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  )
}

function RouteIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  )
}

function LocationIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2C8.134 2 5 5.134 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.866-3.134-7-7-7z" />
      <circle cx="12" cy="9" r="2.5" />
    </svg>
  )
}

function CalendarIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round">
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  )
}

function ExternalLinkIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="7" y1="17" x2="17" y2="7" />
      <polyline points="7 7 17 7 17 17" />
    </svg>
  )
}

function IncidentCard({ incident }) {
  const barColor = SEVERITY_COLOR[incident.crime_macro] ?? '#9ca3af'
  const date     = fmtDate(incident.crime_date)
  const summary  = incident.summary?.length > 180
    ? incident.summary.slice(0, 180).trimEnd() + '…'
    : incident.summary

  return (
    <div className="flex rounded-xl border border-slate-200 bg-white overflow-hidden shadow-sm hover:shadow-md transition-all duration-200">
      {/* Left severity bar */}
      <div className="w-1 flex-shrink-0" style={{ backgroundColor: barColor }} />

      <div className="flex-1 p-3 space-y-2 min-w-0">
        {/* Header: badge + source link */}
        <div className="flex items-center justify-between gap-2">
          <span
            className="text-xs font-semibold px-2 py-0.5 rounded-full text-white flex-shrink-0 leading-relaxed"
            style={{ backgroundColor: barColor }}
          >
            {incident.crime_macro}
          </span>
          {incident.url && (
            <a
              href={incident.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-indigo-500 hover:text-indigo-700 flex-shrink-0 transition-colors"
              onClick={e => e.stopPropagation()}
            >
              Source <ExternalLinkIcon />
            </a>
          )}
        </div>

        {/* Crime sub-type */}
        {incident.crime_type && incident.crime_type !== incident.crime_macro && (
          <p className="text-xs text-slate-400 -mt-1">{incident.crime_type}</p>
        )}

        {/* Summary */}
        {summary && (
          <p className="text-xs text-slate-600 leading-relaxed line-clamp-3">{summary}</p>
        )}

        {/* Meta row */}
        <div className="flex items-center gap-3 text-xs text-slate-400 flex-wrap">
          {incident.location_exact && (
            <div className="flex items-center gap-1 min-w-0">
              <LocationIcon />
              <span className="truncate max-w-[120px]">{incident.location_exact}</span>
            </div>
          )}
          {date && (
            <div className="flex items-center gap-1 flex-shrink-0">
              <CalendarIcon />
              <span>{date}</span>
            </div>
          )}
        </div>

        {incident.victim && (
          <p className="text-xs text-slate-400">Victim: {incident.victim}</p>
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
  const [showQuestionnaire, setShowQuestionnaire]   = useState(false)
  const [answers, setAnswers]                       = useState({ travelling_with: null, transport_mode: null, destination_type: null })
  const [loadingPersonalised, setLoadingPersonalised] = useState(false)

  // Reset personalised state whenever the user switches to a different route
  useEffect(() => {
    onPersonalisedIncidents(null)
    setShowQuestionnaire(false)
    setAnswers({ travelling_with: null, transport_mode: null, destination_type: null })
  }, [selectedIdx])

  if (!routes.length) return null

  const selectedRoute = routes[selectedIdx]
  const incidents     = selectedRoute?.nearby_incidents ?? []
  const allAnswered   = answers.travelling_with && answers.transport_mode && answers.destination_type

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
  const isPersonalised   = personalisedIncidents !== null

  return (
    <div className="space-y-4">

      {/* Section header */}
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
          Route Recommendations
        </p>
        <span className="text-xs font-medium text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
          {routes.length} route{routes.length > 1 ? 's' : ''}
        </span>
      </div>

      {/* Route cards */}
      <div className="space-y-2.5">
        {routes.map((route, i) => {
          const meta  = ROUTE_META[i] ?? { icon: '↗', label: `Alternative Route ${i + 1}`, insight: 'Additional route option' }
          const badge = BADGE[route.risk_band] ?? BADGE.Medium
          return (
            <button
              key={i}
              type="button"
              onClick={() => onSelect(i)}
              className={`w-full text-left rounded-2xl border p-4 transition-all duration-200 ${
                selectedIdx === i
                  ? 'border-indigo-400 bg-indigo-50 shadow-md ring-2 ring-indigo-200'
                  : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'
              }`}
            >
              {/* Top row: label + badge */}
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-base leading-none select-none">{meta.icon}</span>
                  <span className="text-sm font-semibold text-slate-900">{meta.label}</span>
                </div>
                <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full flex-shrink-0 ${badge.bg}`}>
                  <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${badge.dot}`} />
                  <span className={`text-xs font-semibold ${badge.text}`}>{route.risk_band} Risk</span>
                </div>
              </div>

              {/* Stats row */}
              <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                <ClockIcon />
                <span>{fmtDuration(route.duration_sec)}</span>
                <span className="text-slate-300">·</span>
                <RouteIcon />
                <span>{fmtDistance(route.distance_m)}</span>
              </div>

              {/* Insight */}
              <p className="mt-1.5 text-xs text-slate-400">{meta.insight}</p>
            </button>
          )
        })}
      </div>

      {/* Incident section */}
      {(displayIncidents.length > 0 || isPersonalised) && (
        <div className="space-y-3 pt-1">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
              {isPersonalised ? 'Matched to your situation' : 'Nearby incidents'}
            </p>
            {!isPersonalised && (
              <span className="text-xs font-medium text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                {displayIncidents.length}
              </span>
            )}
          </div>

          {displayIncidents.length === 0 && isPersonalised
            ? <p className="text-xs text-slate-400 py-1">No matching incidents found for your situation.</p>
            : displayIncidents.map((incident, i) => <IncidentCard key={i} incident={incident} />)
          }

          {isPersonalised ? (
            <button
              onClick={() => onPersonalisedIncidents(null)}
              className="text-xs text-slate-400 hover:text-slate-600 transition-colors pt-1"
            >
              ← Reset to general results
            </button>
          ) : (
            <p className="text-xs text-slate-300 leading-tight">
              Historical incidents from news sources. Not a prediction of future crime.
            </p>
          )}
        </div>
      )}

      {/* Personalise CTA */}
      {!isPersonalised && !showQuestionnaire && !loadingPersonalised && (
        <button
          onClick={() => setShowQuestionnaire(true)}
          className="flex items-center gap-1.5 text-xs font-medium text-indigo-500 hover:text-indigo-700 transition-colors"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2.5" strokeLinecap="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="16" />
            <line x1="8" y1="12" x2="16" y2="12" />
          </svg>
          Personalise these results
        </button>
      )}

      {/* Loading state */}
      {loadingPersonalised && (
        <p className="text-xs text-slate-400 flex items-center gap-2">
          <svg className="animate-spin flex-shrink-0" width="12" height="12" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M21 12a9 9 0 11-6.219-8.56" />
          </svg>
          Finding personalised incidents…
        </p>
      )}

      {/* Questionnaire */}
      {showQuestionnaire && (
        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm p-4 space-y-4">
          <p className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
            Tell us about your journey
          </p>
          {QUESTIONS.map(q => (
            <div key={q.key}>
              <p className="text-xs font-medium text-slate-600 mb-2">{q.label}</p>
              <div className="flex flex-wrap gap-1.5">
                {q.options.map(opt => (
                  <button
                    key={opt}
                    onClick={() => setAnswers(prev => ({ ...prev, [q.key]: opt }))}
                    className={`px-2.5 py-1 rounded-full text-xs border transition-all duration-150 ${
                      answers[q.key] === opt
                        ? 'bg-indigo-600 text-white border-indigo-600 shadow-sm'
                        : 'bg-white text-slate-500 border-slate-200 hover:border-slate-400 hover:text-slate-700'
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
              className={`px-4 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 ${
                allAnswered
                  ? 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-sm'
                  : 'bg-slate-100 text-slate-300 cursor-not-allowed'
              }`}
            >
              Find incidents like mine
            </button>
            <button
              onClick={() => setShowQuestionnaire(false)}
              className="text-xs text-slate-400 hover:text-slate-600 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
