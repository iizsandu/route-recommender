// frontend/src/components/MapView.jsx
import { useEffect, useRef, useState } from 'react'
import Map, { Source, Layer, NavigationControl, GeolocateControl, Marker, Popup } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

const BAND_COLOR = {
  Low:    '#22c55e',
  Medium: '#f59e0b',
  High:   '#ef4444',
}

// Marker fill color per crime macro — matches RouteResults dot colors
const MACRO_COLOR = {
  'Sexual Violence':   '#dc2626',
  'Kidnapping':        '#ea580c',
  'Robbery':           '#d97706',
  'Assault':           '#ca8a04',
  'Murder':            '#7c3aed',
  'Terrorism / Riot':  '#991b1b',
  'Theft / Burglary':  '#6b7280',
  'Drug / Trafficking':'#9ca3af',
}

const DELHI_CENTER = { longitude: 77.2090, latitude: 28.6139, zoom: 11 }

const MAP_STYLE = `https://api.maptiler.com/maps/streets/style.json?key=${
  import.meta.env.VITE_MAPTILER_KEY ?? ''
}`

const HEATMAP_BOUNDS = [
  [76.5, 29.5],
  [78.0, 29.5],
  [78.0, 28.0],
  [76.5, 28.0],
]

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'
const HEATMAP_IMAGE_URL = `${BASE_URL}/risk/heatmap-image`

// SVG drop-pin for geocoding debug markers (origin = green A, destination = red B)
function PinMarker({ label, color }) {
  return (
    <svg width="28" height="36" viewBox="0 0 28 36" style={{ display: 'block', cursor: 'default' }}>
      <path
        d="M14 0C6.268 0 0 6.268 0 14c0 9.333 14 22 14 22S28 23.333 28 14C28 6.268 21.732 0 14 0z"
        fill={color}
      />
      <circle cx="14" cy="14" r="7" fill="white" />
      <text x="14" y="18" textAnchor="middle" fontSize="9" fontWeight="bold" fill={color}>{label}</text>
    </svg>
  )
}

export default function MapView({ routes, selectedIdx, onSelectRoute, pinLocations }) {
  const mapRef = useRef(null)
  const [showHeatmap, setShowHeatmap] = useState(true)
  // activePopup: index into the selected route's nearby_incidents array, or null
  const [activePopup, setActivePopup] = useState(null)
  const imageLoaded = true
  const imageError  = false

  // Close popup when the selected route changes — incidents change too
  useEffect(() => { setActivePopup(null) }, [selectedIdx])

  // Fly to fit both pins when geocode debug locations are set
  useEffect(() => {
    if (!mapRef.current || !pinLocations?.origin || !pinLocations?.destination) return
    const { origin: o, destination: d } = pinLocations
    mapRef.current.fitBounds(
      [[Math.min(o.lng, d.lng), Math.min(o.lat, d.lat)],
       [Math.max(o.lng, d.lng), Math.max(o.lat, d.lat)]],
      { padding: 80, duration: 800 },
    )
  }, [pinLocations])

  // Fly to fit the selected route
  useEffect(() => {
    if (!mapRef.current || !routes[selectedIdx]) return
    const coords = routes[selectedIdx].geometry.coordinates
    if (!coords?.length) return
    const lngs = coords.map(c => c[0])
    const lats  = coords.map(c => c[1])
    mapRef.current.fitBounds(
      [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
      { padding: 60, duration: 800 },
    )
  }, [selectedIdx, routes])

  // Incidents for the currently selected route — only those with valid coordinates
  const incidents = (routes[selectedIdx]?.nearby_incidents ?? [])
    .filter(inc => inc.lat != null && inc.lng != null)

  return (
    <div className="relative w-full h-full">
      <Map
        ref={mapRef}
        initialViewState={DELHI_CENTER}
        style={{ width: '100%', height: '100%' }}
        mapStyle={MAP_STYLE}
      >
        <NavigationControl position="top-right" />
        <GeolocateControl position="top-right" />

        {/* ── Raster heatmap image ─────────────────────────────────────── */}
        {imageLoaded && (
          <Source
            id="heatmap-raster"
            type="image"
            url={HEATMAP_IMAGE_URL}
            coordinates={HEATMAP_BOUNDS}
          >
            <Layer
              id="heatmap-raster-layer"
              type="raster"
              paint={{
                'raster-resampling': 'linear',
                'raster-opacity': showHeatmap ? [
                  'interpolate', ['linear'], ['zoom'],
                  8,  0.82,
                  13, 0.65,
                  15, 0.25,
                ] : 0,
                'raster-fade-duration': 400,
              }}
            />
          </Source>
        )}

        {/* ── Route polylines ───────────────────────────────────────────── */}
        {[...routes].reverse().map((route, revIdx) => {
          const i = routes.length - 1 - revIdx
          const isSelected = i === selectedIdx
          return (
            <Source key={i} id={`route-${i}`} type="geojson" data={route.geometry}>
              <Layer
                id={`route-${i}-hit`}
                type="line"
                paint={{ 'line-width': 20, 'line-opacity': 0 }}
                onClick={() => onSelectRoute(i)}
              />
              <Layer
                id={`route-${i}-line`}
                type="line"
                paint={{
                  'line-color':   BAND_COLOR[route.risk_band],
                  'line-width':   isSelected ? 6 : 3,
                  'line-opacity': isSelected ? 1 : 0.6,
                }}
              />
            </Source>
          )
        })}

        {/* ── Incident markers for selected route ──────────────────────── */}
        {incidents.map((inc, i) => {
          const color = MACRO_COLOR[inc.crime_macro] ?? '#6b7280'
          return (
            <Marker
              key={i}
              latitude={inc.lat}
              longitude={inc.lng}
              anchor="center"
              onClick={e => {
                // WHY stopPropagation: prevent the map click from firing
                // onSelectRoute when user clicks an incident marker
                e.originalEvent.stopPropagation()
                setActivePopup(activePopup === i ? null : i)
              }}
            >
              {/* WHY inline SVG not a div: Marker children must be DOM elements;
                  SVG gives a crisp circle with a border ring at any zoom level */}
              <svg
                width="14" height="14" viewBox="0 0 14 14"
                style={{ cursor: 'pointer', display: 'block' }}
              >
                <circle cx="7" cy="7" r="5" fill={color} fillOpacity="0.85" />
                <circle cx="7" cy="7" r="6" fill="none" stroke="white" strokeWidth="1.5" />
              </svg>
            </Marker>
          )
        })}

        {/* ── Incident popup ────────────────────────────────────────────── */}
        {activePopup !== null && incidents[activePopup] && (() => {
          const inc = incidents[activePopup]
          const summary = inc.summary?.length > 140
            ? inc.summary.slice(0, 140).trimEnd() + '…'
            : inc.summary
          return (
            <Popup
              latitude={inc.lat}
              longitude={inc.lng}
              anchor="bottom"
              offset={12}
              onClose={() => setActivePopup(null)}
              closeButton={true}
              closeOnClick={false}
              maxWidth="240px"
            >
              <div className="text-xs space-y-1 p-0.5">
                <p className="font-semibold text-gray-800">{inc.crime_macro}</p>
                {summary && <p className="text-gray-600 leading-snug">{summary}</p>}
                {inc.location_exact && (
                  <p className="text-gray-400">{inc.location_exact}</p>
                )}
                {inc.url && (
                  <a
                    href={inc.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-indigo-500 hover:text-indigo-700"
                  >
                    Source ↗
                  </a>
                )}
              </div>
            </Popup>
          )
        })()}

        {/* ── Geocode debug pins (origin A / destination B) ────────────── */}
        {pinLocations?.origin && (
          <Marker
            latitude={pinLocations.origin.lat}
            longitude={pinLocations.origin.lng}
            anchor="bottom"
          >
            <PinMarker label="A" color="#16a34a" />
          </Marker>
        )}
        {pinLocations?.destination && (
          <Marker
            latitude={pinLocations.destination.lat}
            longitude={pinLocations.destination.lng}
            anchor="bottom"
          >
            <PinMarker label="B" color="#dc2626" />
          </Marker>
        )}
      </Map>

      {/* ── Heatmap toggle ───────────────────────────────────────────────── */}
      {!imageError && imageLoaded && (
        <button
          onClick={() => setShowHeatmap(v => !v)}
          className={`absolute top-3 left-3 z-10 flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium shadow-md border transition-all ${
            showHeatmap
              ? 'bg-white border-indigo-300 text-indigo-700'
              : 'bg-white border-gray-200 text-gray-400 hover:text-gray-600'
          }`}
        >
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${showHeatmap ? 'bg-indigo-500' : 'bg-gray-300'}`} />
          Risk heatmap
        </button>
      )}

      {/* ── Legend ───────────────────────────────────────────────────────── */}
      {showHeatmap && imageLoaded && (
        <div className="absolute bottom-8 left-3 z-10 bg-white/90 backdrop-blur-sm rounded-xl shadow-md border border-gray-100 px-3 py-2.5 text-xs">
          <p className="font-semibold text-gray-500 mb-2 uppercase tracking-wide" style={{ fontSize: '10px' }}>
            Historical Crime Risk
          </p>
          <div
            className="w-28 h-2.5 rounded-full mb-1"
            style={{ background: 'linear-gradient(to right, rgba(255,220,80,0.4), rgba(255,100,0,0.8), rgba(140,0,0,0.95))' }}
          />
          <div className="flex justify-between text-gray-400 mb-1" style={{ fontSize: '9px' }}>
            <span>Lower</span><span>Higher</span>
          </div>
          <p className="text-gray-300 leading-tight" style={{ fontSize: '9px' }}>
            Transparent = low risk
          </p>
        </div>
      )}

      {/* ── Loading state ─────────────────────────────────────────────────── */}
      {!imageLoaded && !imageError && (
        <div className="absolute top-3 left-3 z-10 flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs text-gray-400 bg-white/80 shadow-sm border border-gray-100">
          <span className="w-2 h-2 rounded-full bg-gray-300 animate-pulse" />
          Loading heatmap…
        </div>
      )}
    </div>
  )
}
