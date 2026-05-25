// frontend/src/components/MapView.jsx
import { useEffect, useRef, useState } from 'react'
import Map, { Source, Layer, NavigationControl, GeolocateControl, Marker, Popup } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

const BAND_COLOR = {
  Low:    '#22c55e',
  Medium: '#f59e0b',
  High:   '#ef4444',
}

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

// Category pills — id matches the ?category= query param and heatmap_<id>.png filename
const CATEGORIES = [
  { id: 'all',             label: 'All',            color: '#ef4444', gradient: 'rgba(255,220,80,0.4), rgba(255,100,0,0.8), rgba(140,0,0,0.95)' },
  { id: 'sexual_violence', label: 'Sex. Violence',  color: '#dc2626', gradient: 'rgba(255,200,210,0.4), rgba(220,0,50,0.8), rgba(100,0,20,0.95)' },
  { id: 'robbery',         label: 'Robbery',        color: '#d97706', gradient: 'rgba(255,250,180,0.4), rgba(255,160,0,0.8), rgba(140,60,0,0.95)' },
  { id: 'assault',         label: 'Assault',        color: '#b45309', gradient: 'rgba(255,230,200,0.4), rgba(220,110,20,0.8), rgba(100,30,0,0.95)' },
  { id: 'kidnapping',      label: 'Kidnapping',     color: '#7c3aed', gradient: 'rgba(220,200,255,0.4), rgba(140,60,220,0.8), rgba(50,0,110,0.95)' },
  { id: 'murder',          label: 'Murder',         color: '#374151', gradient: 'rgba(210,210,210,0.4), rgba(90,90,90,0.8), rgba(0,0,0,0.95)' },
  { id: 'theft_burglary',  label: 'Theft',          color: '#0d9488', gradient: 'rgba(200,240,235,0.4), rgba(20,180,160,0.8), rgba(0,60,55,0.95)' },
]

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

const HEATMAP_OPACITY = ['interpolate', ['linear'], ['zoom'], 8, 0.60, 13, 0.40, 15, 0.18]

// Remove any existing heatmap source+layer, then re-add from scratch.
// WHY remove/re-add instead of updateImage: updateImage is fire-and-forget —
// MapLibre fetches the new PNG async with no completion callback, so React
// state (activeCategory, legend) can show category X while the map still
// renders category Y's raster. Re-adding the source forces MapLibre to
// finish loading the new image before swapping it in.
function _applyHeatmap(map, category, visible) {
  if (map.getLayer('heatmap-raster-layer')) map.removeLayer('heatmap-raster-layer')
  if (map.getSource('heatmap-raster'))      map.removeSource('heatmap-raster')

  map.addSource('heatmap-raster', {
    type: 'image',
    url: `${BASE_URL}/risk/heatmap-image?category=${category}`,
    coordinates: HEATMAP_BOUNDS,
  })

  // WHY beforeId: without it MapLibre stacks the raster on top of everything,
  // smearing over road labels and POI icons. Inserting before the first symbol
  // layer keeps labels readable while the risk colour shows through underneath.
  const firstSymbol = map.getStyle()?.layers?.find(l => l.type === 'symbol')?.id

  map.addLayer(
    {
      id: 'heatmap-raster-layer',
      type: 'raster',
      source: 'heatmap-raster',
      paint: {
        'raster-resampling': 'linear',
        'raster-opacity': visible ? HEATMAP_OPACITY : 0,
        'raster-fade-duration': 300,
      },
    },
    firstSymbol,
  )
}

export default function MapView({ routes, selectedIdx, onSelectRoute, pinLocations, personalisedIncidents = null }) {
  const mapRef = useRef(null)
  const [showHeatmap, setShowHeatmap]       = useState(true)
  const [activeCategory, setActiveCategory] = useState('all')
  const [activeGeneralPopup, setActiveGeneralPopup]           = useState(null)
  const [activePersonalisedPopup, setActivePersonalisedPopup] = useState(null)
  const [mapReady, setMapReady]             = useState(false)

  const activeCat  = CATEGORIES.find(c => c.id === activeCategory)

  function handleMapLoad() {
    _applyHeatmap(mapRef.current.getMap(), 'all', true)
    setMapReady(true)
  }

  // Swap heatmap when category changes — full source/layer replacement for determinism.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    _applyHeatmap(mapRef.current.getMap(), activeCategory, showHeatmap)
  }, [activeCategory, mapReady])

  // Toggle heatmap visibility
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current.getMap()
    if (!map.getLayer('heatmap-raster-layer')) return
    map.setPaintProperty('heatmap-raster-layer', 'raster-opacity', showHeatmap ? HEATMAP_OPACITY : 0)
  }, [showHeatmap, mapReady])

  useEffect(() => { setActiveGeneralPopup(null); setActivePersonalisedPopup(null) }, [selectedIdx])

  useEffect(() => {
    if (!mapRef.current || !pinLocations?.origin || !pinLocations?.destination) return
    const { origin: o, destination: d } = pinLocations
    mapRef.current.fitBounds(
      [[Math.min(o.lng, d.lng), Math.min(o.lat, d.lat)],
       [Math.max(o.lng, d.lng), Math.max(o.lat, d.lat)]],
      { padding: 80, duration: 800 },
    )
  }, [pinLocations])

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

  const generalIncidents = (routes[selectedIdx]?.nearby_incidents ?? [])
    .filter(inc => inc.lat != null && inc.lng != null)
  const personalisedDots = (personalisedIncidents ?? [])
    .filter(inc => inc.lat != null && inc.lng != null)

  return (
    <div className="relative w-full h-full">
      <Map
        ref={mapRef}
        initialViewState={DELHI_CENTER}
        style={{ width: '100%', height: '100%' }}
        mapStyle={MAP_STYLE}
        onLoad={handleMapLoad}
      >
        <NavigationControl position="top-right" />
        <GeolocateControl position="top-right" />

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

        {/* ── General incident markers ──────────────────────────────────── */}
        {generalIncidents.map((inc, i) => {
          const color = MACRO_COLOR[inc.crime_macro] ?? '#6b7280'
          return (
            <Marker
              key={`g-${i}`}
              latitude={inc.lat}
              longitude={inc.lng}
              anchor="center"
              onClick={e => {
                e.originalEvent.stopPropagation()
                setActiveGeneralPopup(activeGeneralPopup === i ? null : i)
                setActivePersonalisedPopup(null)
              }}
            >
              <svg width="18" height="18" viewBox="0 0 18 18" style={{ cursor: 'pointer', display: 'block' }}>
                <circle cx="9" cy="9" r="7" fill={color} fillOpacity="0.9" />
                <circle cx="9" cy="9" r="7" fill="none" stroke="white" strokeWidth="2" />
              </svg>
            </Marker>
          )
        })}

        {/* ── Personalised incident markers ─────────────────────────────── */}
        {personalisedDots.length > 0 && personalisedDots.map((inc, i) => (
          <Marker
            key={`p-${i}`}
            latitude={inc.lat}
            longitude={inc.lng}
            anchor="center"
            onClick={e => {
              e.originalEvent.stopPropagation()
              setActivePersonalisedPopup(activePersonalisedPopup === i ? null : i)
              setActiveGeneralPopup(null)
            }}
          >
            <svg
              width="40"
              height="40"
              viewBox="0 0 40 40"
              style={{ cursor: 'pointer', display: 'block', overflow: 'visible' }}
            >
              <circle cx="20" cy="20" fill="#7f77dd" fillOpacity="0.15" stroke="none">
                <animate attributeName="r" values="9;28;28" dur="2s" repeatCount="indefinite" begin="0.3s" />
                <animate attributeName="fill-opacity" values="0.15;0;0" dur="2s" repeatCount="indefinite" begin="0.3s" />
              </circle>
              <circle cx="20" cy="20" fill="#7f77dd" fillOpacity="0.3" stroke="none">
                <animate attributeName="r" values="9;20;20" dur="2s" repeatCount="indefinite" />
                <animate attributeName="fill-opacity" values="0.3;0;0" dur="2s" repeatCount="indefinite" />
              </circle>
              <circle cx="20" cy="20" r="9" fill="#7f77dd" />
              <circle cx="20" cy="20" r="9" fill="none" stroke="white" strokeWidth="2" />
            </svg>
          </Marker>
        ))}

        {/* ── General incident popup ────────────────────────────────────── */}
        {activeGeneralPopup !== null && generalIncidents[activeGeneralPopup] && (() => {
          const inc = generalIncidents[activeGeneralPopup]
          const summary = inc.summary?.length > 140
            ? inc.summary.slice(0, 140).trimEnd() + '…'
            : inc.summary
          return (
            <Popup
              latitude={inc.lat}
              longitude={inc.lng}
              anchor="bottom"
              offset={12}
              onClose={() => setActiveGeneralPopup(null)}
              closeButton={true}
              closeOnClick={false}
              maxWidth="240px"
            >
              <div className="text-xs space-y-1 p-0.5">
                <p className="font-semibold text-gray-800">{inc.crime_macro}</p>
                {summary && <p className="text-gray-600 leading-snug">{summary}</p>}
                {inc.location_exact && <p className="text-gray-400">{inc.location_exact}</p>}
                {inc.victim && <p className="text-gray-400">Victim: {inc.victim}</p>}
                {inc.url && (
                  <a href={inc.url} target="_blank" rel="noopener noreferrer" className="text-indigo-500 hover:text-indigo-700">
                    Source ↗
                  </a>
                )}
              </div>
            </Popup>
          )
        })()}

        {/* ── Personalised incident popup ───────────────────────────────── */}
        {activePersonalisedPopup !== null && personalisedDots[activePersonalisedPopup] && (() => {
          const inc = personalisedDots[activePersonalisedPopup]
          const summary = inc.summary?.length > 140
            ? inc.summary.slice(0, 140).trimEnd() + '…'
            : inc.summary
          return (
            <Popup
              latitude={inc.lat}
              longitude={inc.lng}
              anchor="bottom"
              offset={12}
              onClose={() => setActivePersonalisedPopup(null)}
              closeButton={true}
              closeOnClick={false}
              maxWidth="240px"
            >
              <div className="text-xs space-y-1 p-0.5">
                <p className="font-semibold text-gray-800">{inc.crime_macro}</p>
                {summary && <p className="text-gray-600 leading-snug">{summary}</p>}
                {inc.location_exact && <p className="text-gray-400">{inc.location_exact}</p>}
                {inc.victim && <p className="text-gray-400">Victim: {inc.victim}</p>}
                {inc.url && (
                  <a href={inc.url} target="_blank" rel="noopener noreferrer" className="text-indigo-500 hover:text-indigo-700">
                    Source ↗
                  </a>
                )}
              </div>
            </Popup>
          )
        })()}

        {/* ── Origin / destination pins ─────────────────────────────────── */}
        {pinLocations?.origin && (
          <Marker latitude={pinLocations.origin.lat} longitude={pinLocations.origin.lng} anchor="bottom">
            <PinMarker label="A" color="#16a34a" />
          </Marker>
        )}
        {pinLocations?.destination && (
          <Marker latitude={pinLocations.destination.lat} longitude={pinLocations.destination.lng} anchor="bottom">
            <PinMarker label="B" color="#dc2626" />
          </Marker>
        )}
      </Map>

      {/* ── Heatmap controls (toggle + category pills) ───────────────────── */}
      {mapReady && (
        <div className="absolute top-3 left-3 z-10 flex flex-col gap-2">
          {/* Toggle button */}
          <button
            onClick={() => setShowHeatmap(v => !v)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium shadow-md border transition-all bg-white ${
              showHeatmap
                ? 'border-indigo-300 text-indigo-700'
                : 'border-gray-200 text-gray-400 hover:text-gray-600'
            }`}
          >
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${showHeatmap ? 'bg-indigo-500' : 'bg-gray-300'}`} />
            Risk heatmap
          </button>

          {/* Category pills — only visible when heatmap is on */}
          {showHeatmap && (
            <div className="bg-white/95 backdrop-blur-sm rounded-xl shadow-md border border-gray-100 p-2 flex flex-wrap gap-1 max-w-[200px]">
              {CATEGORIES.map(cat => (
                <button
                  key={cat.id}
                  onClick={() => setActiveCategory(cat.id)}
                  className="px-2 py-0.5 rounded-full text-white transition-all"
                  style={{
                    fontSize: '10px',
                    fontWeight: activeCategory === cat.id ? 700 : 400,
                    backgroundColor: activeCategory === cat.id ? cat.color : '#d1d5db',
                    opacity: activeCategory === cat.id ? 1 : 0.75,
                  }}
                >
                  {cat.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Legend ───────────────────────────────────────────────────────── */}
      {showHeatmap && mapReady && (
        <div className="absolute bottom-8 left-3 z-10 bg-white/90 backdrop-blur-sm rounded-xl shadow-md border border-gray-100 px-3 py-2.5 text-xs">
          <p className="font-semibold text-gray-500 mb-2 uppercase tracking-wide" style={{ fontSize: '10px' }}>
            {activeCat?.label ?? 'All'} Risk
          </p>
          <div
            className="w-28 h-2.5 rounded-full mb-1"
            style={{ background: `linear-gradient(to right, ${activeCat?.gradient ?? CATEGORIES[0].gradient})` }}
          />
          <div className="flex justify-between text-gray-400 mb-1" style={{ fontSize: '9px' }}>
            <span>Lower</span><span>Higher</span>
          </div>
          <p className="text-gray-300 leading-tight" style={{ fontSize: '9px' }}>
            Transparent = low risk
          </p>
        </div>
      )}
    </div>
  )
}
