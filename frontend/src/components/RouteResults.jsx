// frontend/src/components/RouteResults.jsx

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

export default function RouteResults({ routes, selectedIdx, onSelect }) {
  if (!routes.length) return null

  const selectedRoute = routes[selectedIdx]
  const incidents = selectedRoute?.nearby_incidents ?? []

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

      {/* Incident drawer — only shown when selected route has incidents */}
      {incidents.length > 0 && (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            {incidents.length} nearby historical incident{incidents.length > 1 ? 's' : ''}
          </p>
          {incidents.map((incident, i) => (
            <IncidentCard key={i} incident={incident} />
          ))}
          <p className="text-xs text-gray-300 leading-tight pt-1">
            Historically reported incidents from news sources. Not a prediction of future crime.
          </p>
        </div>
      )}
    </div>
  )
}
