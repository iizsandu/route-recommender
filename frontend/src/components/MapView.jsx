// frontend/src/components/MapView.jsx
import { useEffect, useRef, useState } from 'react'
import Map, { Source, Layer, NavigationControl, GeolocateControl, Marker, Popup } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

// Route line color by type — safest gets blue/teal, fastest gets amber.
const ROUTE_TYPE_COLOR = {
  safest:      '#6366f1',   // indigo
  fastest:     '#f59e0b',   // amber
  alternative: '#94a3b8',   // slate
}

const MACRO_COLOR = {
  'Sexual Violence':    '#dc2626',
  'Kidnapping':         '#ea580c',
  'Robbery':            '#d97706',
  'Assault':            '#ca8a04',
  'Murder':             '#7c3aed',
  'Terrorism / Riot':   '#991b1b',
  'Theft / Burglary':   '#6b7280',
  'Drug / Trafficking': '#9ca3af',
}

// Category pills — id matches the ?category= query param and heatmap_<id>.png filename
const CATEGORIES = [
  { id: 'all',             label: 'All',           color: '#ef4444', gradient: 'rgba(255,220,80,0.4), rgba(255,100,0,0.8), rgba(140,0,0,0.95)' },
  { id: 'sexual_violence', label: 'Sex. Violence', color: '#dc2626', gradient: 'rgba(255,200,210,0.4), rgba(220,0,50,0.8), rgba(100,0,20,0.95)' },
  { id: 'robbery',         label: 'Robbery',       color: '#d97706', gradient: 'rgba(255,250,180,0.4), rgba(255,160,0,0.8), rgba(140,60,0,0.95)' },
  { id: 'assault',         label: 'Assault',       color: '#b45309', gradient: 'rgba(255,230,200,0.4), rgba(220,110,20,0.8), rgba(100,30,0,0.95)' },
  { id: 'kidnapping',      label: 'Kidnapping',    color: '#7c3aed', gradient: 'rgba(220,200,255,0.4), rgba(140,60,220,0.8), rgba(50,0,110,0.95)' },
  { id: 'murder',          label: 'Murder',        color: '#374151', gradient: 'rgba(210,210,210,0.4), rgba(90,90,90,0.8), rgba(0,0,0,0.95)' },
  { id: 'theft_burglary',  label: 'Theft',         color: '#0d9488', gradient: 'rgba(200,240,235,0.4), rgba(20,180,160,0.8), rgba(0,60,55,0.95)' },
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

// ── Popup card helpers ────────────────────────────────────────────────────────

function getSeverityConfig(crime_macro) {
  if (['Murder', 'Sexual Violence', 'Kidnapping', 'Terrorism / Riot'].includes(crime_macro))
    return { label: 'HIGH RISK',   color: '#ef4444', bg: '#fef2f2', textColor: 'text-red-600' }
  if (['Robbery', 'Assault'].includes(crime_macro))
    return { label: 'MEDIUM RISK', color: '#f59e0b', bg: '#fffbeb', textColor: 'text-amber-600' }
  return   { label: 'ALERT',       color: '#3b82f6', bg: '#eff6ff', textColor: 'text-blue-600' }
}

function getCrimeIcon(crime_macro) {
  const s = { stroke: 'white', strokeWidth: '1.8', strokeLinecap: 'round', strokeLinejoin: 'round', fill: 'none' }
  switch (crime_macro) {
    case 'Murder':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" {...s}>
          <path d="M12 4C8.134 4 5 7.134 5 11c0 2.387 1.21 4.49 3.05 5.76V19h7.9v-2.24C17.79 15.49 19 13.387 19 11c0-3.866-3.134-7-7-7z"/>
          <circle cx="9.5" cy="11.5" r="1" fill="white" stroke="none"/>
          <circle cx="14.5" cy="11.5" r="1" fill="white" stroke="none"/>
          <path d="M9.5 19v-1.5h5V19"/>
        </svg>
      )
    case 'Robbery': case 'Theft / Burglary':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" {...s}>
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          <path d="M9 12l2 2 4-4"/>
        </svg>
      )
    case 'Kidnapping':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" {...s}>
          <rect x="5" y="11" width="14" height="11" rx="2"/>
          <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          <circle cx="12" cy="16" r="1" fill="white" stroke="none"/>
        </svg>
      )
    case 'Sexual Violence':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" {...s}>
          <circle cx="12" cy="12" r="9"/>
          <line x1="12" y1="8" x2="12" y2="13"/>
          <circle cx="12" cy="16.5" r="0.8" fill="white" stroke="none"/>
        </svg>
      )
    case 'Assault':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" {...s}>
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
          <line x1="12" y1="9" x2="12" y2="13"/>
          <circle cx="12" cy="17" r="0.8" fill="white" stroke="none"/>
        </svg>
      )
    default:
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" {...s}>
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
          <line x1="12" y1="9" x2="12" y2="13"/>
          <circle cx="12" cy="17" r="0.8" fill="white" stroke="none"/>
        </svg>
      )
  }
}

function fmtPopupDate(dateStr) {
  if (!dateStr) return null
  const d = new Date(dateStr)
  if (isNaN(d)) return null
  return d.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })
}

function IncidentPopupCard({ inc, onClose, onGetDetails }) {
  const sev     = getSeverityConfig(inc.crime_macro)
  const date    = fmtPopupDate(inc.crime_date)
  const summary = inc.summary?.length > 200
    ? inc.summary.slice(0, 200).trimEnd() + '…'
    : inc.summary

  return (
    <div className="flex flex-col bg-white rounded-2xl overflow-hidden w-[320px] transition-all">
      <div className="flex">
        {/* ── Left severity strip ─────────────────────────────── */}
        <div
          className="flex flex-col items-center justify-start pt-4 pb-3 px-2.5 gap-2.5 min-w-[68px]"
          style={{ backgroundColor: sev.bg }}
        >
          {/* Icon hex */}
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ backgroundColor: sev.color }}
          >
            {getCrimeIcon(inc.crime_macro)}
          </div>

          {/* Risk label */}
          <span
            className={`text-center font-bold uppercase leading-tight ${sev.textColor}`}
            style={{ fontSize: '8px', letterSpacing: '0.04em' }}
          >
            {sev.label}
          </span>
        </div>

        {/* ── Right content ────────────────────────────────────── */}
        <div className="flex-1 p-3 min-w-0 space-y-1.5">
          {/* Title row */}
          <div className="flex items-start justify-between gap-1">
            <h3 className="text-sm font-bold text-gray-900 leading-tight">{inc.crime_macro}</h3>
            <button
              onClick={onClose}
              className="text-gray-300 hover:text-gray-500 transition-colors flex-shrink-0 mt-0.5 p-0.5"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="1" y1="1" x2="11" y2="11"/><line x1="11" y1="1" x2="1" y2="11"/>
              </svg>
            </button>
          </div>

          {/* Crime sub-type */}
          {inc.crime_type && inc.crime_type !== inc.crime_macro && (
            <p className="text-xs text-gray-400 -mt-0.5">{inc.crime_type}</p>
          )}

          {/* Summary */}
          {summary && (
            <p className="text-xs text-gray-600 leading-relaxed line-clamp-3">{summary}</p>
          )}

          {/* Metadata rows */}
          <div className="space-y-1 pt-0.5">
            {date && (
              <div className="flex items-center gap-1.5 text-xs text-gray-400">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
                </svg>
                <span>{date}</span>
              </div>
            )}
            {inc.location_exact && (
              <div className="flex items-center gap-1.5 text-xs text-gray-400">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2C8.134 2 5 5.134 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.866-3.134-7-7-7z"/><circle cx="12" cy="9" r="2.5"/>
                </svg>
                <span className="truncate max-w-[160px]">{inc.location_exact}</span>
              </div>
            )}
            {inc.victim && (
              <div className="flex items-center gap-1.5 text-xs text-gray-400">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <circle cx="12" cy="7" r="4"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>
                </svg>
                <span className="truncate max-w-[160px]">{inc.victim}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Footer: Get details | View report ───────────────────── */}
      <div className="border-t border-gray-100 flex">
        {/* Get details — opens the full detail panel */}
        <button
          onClick={e => { e.stopPropagation(); onGetDetails && onGetDetails(inc) }}
          className="flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-semibold text-slate-600 hover:bg-slate-50 transition-colors border-r border-gray-100"
        >
          Get details
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
          </svg>
        </button>
        {/* View source link — conditional on URL availability */}
        {inc.url ? (
          <a
            href={inc.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-semibold hover:bg-gray-50 transition-colors"
            style={{ color: sev.color }}
            onClick={e => e.stopPropagation()}
          >
            View report
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
            </svg>
          </a>
        ) : (
          <div className="flex-1 flex items-center justify-center py-2.5">
            <p className="text-xs text-gray-300">No source</p>
          </div>
        )}
      </div>
    </div>
  )
}

// Full-screen slide-in panel — covers the right 40% of the viewport.
// The left 60% is blurred and acts as a click-to-close backdrop.
function CrimeDetailPanel({ inc, onClose }) {
  const sev = getSeverityConfig(inc.crime_macro)

  function safeDate(dateStr) {
    if (!dateStr) return null
    const d = new Date(dateStr)
    if (isNaN(d)) return null
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })
  }

  return (
    <>
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to   { transform: translateX(0); }
        }
        .cdp-slide { animation: slideInRight 0.22s cubic-bezier(0.22,1,0.36,1); }
      `}</style>

      {/* Full-screen container — click the blurred area to close */}
      <div className="fixed inset-0 z-[200] flex" onClick={onClose}>

        {/* Blurred backdrop (left 60%) */}
        <div className="flex-1 backdrop-blur-[3px] bg-black/20" />

        {/* Detail panel (right 40%) — stopPropagation prevents backdrop close */}
        <div
          className="cdp-slide w-[40%] min-w-[320px] bg-white h-full shadow-2xl flex flex-col overflow-hidden"
          onClick={e => e.stopPropagation()}
        >
          {/* ── Header ─────────────────────────────────────────────────── */}
          <div
            className="flex items-center justify-between px-6 py-4 border-b border-gray-100 flex-shrink-0"
            style={{ backgroundColor: sev.bg }}
          >
            <div className="flex items-center gap-3">
              <div
                className="w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0"
                style={{ backgroundColor: sev.color }}
              >
                {getCrimeIcon(inc.crime_macro)}
              </div>
              <div>
                <h2 className="font-bold text-gray-900 text-base leading-tight">{inc.crime_macro}</h2>
                <span className={`text-[10px] font-bold uppercase tracking-widest ${sev.textColor}`}>
                  {sev.label}
                </span>
              </div>
            </div>
            <button
              onClick={onClose}
              className="w-8 h-8 rounded-full flex items-center justify-center hover:bg-black/10 transition-colors flex-shrink-0"
              aria-label="Close"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none"
                stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <line x1="1" y1="1" x2="13" y2="13" />
                <line x1="13" y1="1" x2="1" y2="13" />
              </svg>
            </button>
          </div>

          {/* ── Body (scrollable) ───────────────────────────────────────── */}
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">

            {inc.crime_type && inc.crime_type !== inc.crime_macro && (
              <div>
                <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-1">Crime Type</p>
                <p className="text-sm text-gray-800">{inc.crime_type}</p>
              </div>
            )}

            {safeDate(inc.crime_date) && (
              <div>
                <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-1">Date</p>
                <p className="text-sm text-gray-800">{safeDate(inc.crime_date)}</p>
              </div>
            )}

            {inc.location_exact && (
              <div>
                <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-1">Location</p>
                <p className="text-sm text-gray-800">{inc.location_exact}</p>
              </div>
            )}

            {inc.victim && (
              <div>
                <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-1">Victim</p>
                <p className="text-sm text-gray-800">{inc.victim}</p>
              </div>
            )}

            {inc.weapon_used && (
              <div>
                <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-1">Weapon Used</p>
                <p className="text-sm text-gray-800">{inc.weapon_used}</p>
              </div>
            )}

            {inc.summary && (
              <div>
                <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-1">Summary</p>
                <p className="text-sm text-gray-700 leading-relaxed">{inc.summary}</p>
              </div>
            )}

            {/* Fallback when record has no details beyond the category */}
            {!inc.crime_type && !inc.crime_date && !inc.location_exact &&
             !inc.victim && !inc.weapon_used && !inc.summary && (
              <p className="text-sm text-gray-400 italic">
                No additional details available for this record.
              </p>
            )}
          </div>

          {/* ── Footer — source link ────────────────────────────────────── */}
          <div className="px-6 py-4 border-t border-gray-100 flex-shrink-0">
            {inc.url ? (
              <a
                href={inc.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-center gap-2 w-full py-3 rounded-xl text-sm font-semibold text-white transition-opacity hover:opacity-90"
                style={{ backgroundColor: sev.color }}
                onClick={e => e.stopPropagation()}
              >
                View Full Report
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="5" y1="12" x2="19" y2="12" />
                  <polyline points="12 5 19 12 12 19" />
                </svg>
              </a>
            ) : (
              <p className="text-sm text-gray-400 text-center">No source article available</p>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

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

// ── Overlay sub-components ────────────────────────────────────────────────────

function HeatmapControl({ showHeatmap, onToggle, activeCategory, onCategoryChange }) {
  return (
    <div className="bg-white rounded-2xl shadow-md border border-slate-200 overflow-hidden min-w-[192px]">
      {/* Toggle row */}
      <button
        onClick={onToggle}
        className="flex items-center justify-between gap-3 w-full px-4 py-3 hover:bg-slate-50 transition-colors duration-150"
      >
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full flex-shrink-0 transition-colors duration-200 ${showHeatmap ? 'bg-indigo-500' : 'bg-slate-300'}`} />
          <span className={`text-sm font-medium transition-colors duration-200 ${showHeatmap ? 'text-slate-800' : 'text-slate-400'}`}>
            Crime Heatmap
          </span>
        </div>
        {/* Toggle switch */}
        <div className={`relative w-9 h-5 rounded-full transition-colors duration-200 flex-shrink-0 ${showHeatmap ? 'bg-indigo-600' : 'bg-slate-200'}`}>
          <div className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${showHeatmap ? 'translate-x-[17px]' : 'translate-x-0.5'}`} />
        </div>
      </button>

      {/* Category grid — only when heatmap is on */}
      {showHeatmap && (
        <div className="px-3 pb-3 border-t border-slate-100 pt-2.5">
          {/* All button full width */}
          <button
            onClick={() => onCategoryChange('all')}
            className="w-full px-2 py-1.5 rounded-lg text-xs font-medium mb-1 transition-all duration-150"
            style={{
              backgroundColor: activeCategory === 'all' ? CATEGORIES[0].color : '#f1f5f9',
              color: activeCategory === 'all' ? 'white' : '#64748b',
            }}
          >
            All Crime Types
          </button>
          {/* Other categories in a 2-column grid */}
          <div className="grid grid-cols-2 gap-1">
            {CATEGORIES.slice(1).map(cat => (
              <button
                key={cat.id}
                onClick={() => onCategoryChange(cat.id)}
                className="px-2 py-1.5 rounded-lg text-left transition-all duration-150"
                style={{
                  fontSize: '11px',
                  fontWeight: activeCategory === cat.id ? 600 : 400,
                  backgroundColor: activeCategory === cat.id ? cat.color : '#f1f5f9',
                  color: activeCategory === cat.id ? 'white' : '#64748b',
                }}
              >
                {cat.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function RiskLegend({ activeCat }) {
  return (
    <div className="bg-white rounded-2xl shadow-md border border-slate-200 px-4 py-3.5 min-w-[160px]">
      <p className="text-xs font-semibold text-slate-700 mb-3">Crime Risk Levels</p>
      <div className="space-y-2">
        <div className="flex items-center gap-2.5">
          <div className="w-3 h-3 rounded-full bg-emerald-500 flex-shrink-0" />
          <span className="text-xs text-slate-600 font-medium">Low Risk</span>
        </div>
        <div className="flex items-center gap-2.5">
          <div className="w-3 h-3 rounded-full bg-amber-500 flex-shrink-0" />
          <span className="text-xs text-slate-600 font-medium">Medium Risk</span>
        </div>
        <div className="flex items-center gap-2.5">
          <div className="w-3 h-3 rounded-full bg-red-500 flex-shrink-0" />
          <span className="text-xs text-slate-600 font-medium">High Risk</span>
        </div>
      </div>
      {activeCat && (
        <div className="mt-3 pt-3 border-t border-slate-100">
          <div
            className="w-full h-2 rounded-full"
            style={{ background: `linear-gradient(to right, ${activeCat.gradient})` }}
          />
          <div className="flex justify-between text-slate-400 mt-1" style={{ fontSize: '9px' }}>
            <span>Lower</span><span>Higher</span>
          </div>
        </div>
      )}
    </div>
  )
}

function RouteLegend({ routes }) {
  const entries = [
    { type: 'fastest', label: 'Fastest Route' },
    { type: 'safest',  label: 'Safest Route'  },
  ].filter(e => routes.some(r => r.route_type === e.type))
  if (!entries.length) return null
  return (
    <div className="bg-white rounded-2xl shadow-md border border-slate-200 px-4 py-3.5 min-w-[140px]">
      <div className="space-y-2">
        {entries.map(e => (
          <div key={e.type} className="flex items-center gap-2.5">
            <svg width="24" height="4" style={{ flexShrink: 0 }}>
              <line x1="0" y1="2" x2="24" y2="2" stroke={ROUTE_TYPE_COLOR[e.type]} strokeWidth="3" strokeLinecap="round" />
            </svg>
            <span className="text-xs text-slate-600">{e.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

// MapLibre match expression — colors individual crime dots by crime_macro.
// Must be a plain array (MapLibre spec), not a JS object.
const CRIME_COLOR_EXPR = [
  'match', ['get', 'crime_macro'],
  'Sexual Violence',    '#dc2626',
  'Kidnapping',         '#ea580c',
  'Robbery',            '#d97706',
  'Assault',            '#ca8a04',
  'Murder',             '#7c3aed',
  'Terrorism / Riot',   '#991b1b',
  'Theft / Burglary',   '#6b7280',
  'Drug / Trafficking', '#9ca3af',
  '#6b7280',  // default
]

export default function MapView({ routes, selectedIdx, onSelectRoute, pinLocations, personalisedIncidents = null }) {
  const mapRef = useRef(null)
  const [showHeatmap, setShowHeatmap]       = useState(true)
  const [activeCategory, setActiveCategory] = useState('all')
  const [activeGeneralPopup, setActiveGeneralPopup]           = useState(null)
  const [activePersonalisedPopup, setActivePersonalisedPopup] = useState(null)
  const [mapReady, setMapReady]             = useState(false)

  // All-crimes layer state
  const [showAllCrimes, setShowAllCrimes]       = useState(false)
  const [allCrimesData, setAllCrimesData]       = useState(null)   // GeoJSON once fetched
  const [allCrimesLoading, setAllCrimesLoading] = useState(false)
  const [activeCrimePopup, setActiveCrimePopup] = useState(null)   // {lat, lng, inc}

  // Crime detail panel — full slide-in panel triggered by "Get details" in any popup
  const [crimeDetailInc, setCrimeDetailInc] = useState(null)

  const activeCat = CATEGORIES.find(c => c.id === activeCategory)

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

  useEffect(() => {
    setActiveGeneralPopup(null)
    setActivePersonalisedPopup(null)
    // WHY: activeCrimePopup is independent of route selection — it belongs to the
    // all-crimes layer which is not tied to any particular route. Clearing it here
    // caused the popup to vanish immediately whenever a route click fired nearby.
  }, [selectedIdx])

  async function handleToggleAllCrimes() {
    const next = !showAllCrimes
    setShowAllCrimes(next)
    if (next && !allCrimesData) {
      setAllCrimesLoading(true)
      try {
        const res = await fetch(`${BASE_URL}/risk/crimes-geojson`)
        const data = await res.json()
        setAllCrimesData(data)
      } catch (e) {
        console.error('Failed to load crimes GeoJSON', e)
      } finally {
        setAllCrimesLoading(false)
      }
    }
  }

  // Single map-level click handler for all interactive layers.
  // WHY centralised: react-map-gl v7 only populates e.features when the clicked
  // layer is listed in interactiveLayerIds on <Map>. onClick on individual <Layer>
  // components does NOT receive e.features — the handler would always exit early
  // at the `if (!feature) return` guard. Centralising here is the correct pattern.
  function handleMapClick(e) {
    const feature = e.features?.[0]
    if (!feature) return

    const lid = feature.layer.id

    // Route hit area — extract index from id pattern "route-{i}-hit"
    if (lid.endsWith('-hit')) {
      const idx = parseInt(lid.split('-')[1], 10)
      onSelectRoute(idx)
      return
    }

    // Cluster bubble — zoom in to expand
    if (lid === 'all-crimes-clusters') {
      const map = mapRef.current.getMap()
      const src = map.getSource('all-crimes-src')
      src.getClusterExpansionZoom(feature.properties.cluster_id, (err, zoom) => {
        if (err) return
        map.easeTo({ center: feature.geometry.coordinates, zoom: zoom + 0.5, duration: 400 })
      })
      return
    }

    // Individual crime dot — show popup card
    if (lid === 'all-crimes-unclustered') {
      const p = feature.properties
      setActiveCrimePopup({
        lat: feature.geometry.coordinates[1],
        lng: feature.geometry.coordinates[0],
        inc: {
          crime_macro:    p.crime_macro    || null,
          crime_type:     p.crime_type     || null,
          crime_date:     p.crime_date     || null,
          summary:        '',
          url:            p.url            || '',
          location_exact: p.location_exact || null,
          victim:         p.victim         || null,
          weapon_used:    p.weapon_used    || null,
          rrf_score:      0,
        },
      })
      setActiveGeneralPopup(null)
      setActivePersonalisedPopup(null)
    }
  }

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
        onClick={handleMapClick}
        interactiveLayerIds={[
          // Route hit areas — one per route, always registered when routes exist
          ...routes.map((_, i) => `route-${i}-hit`),
          // All-crimes layers — only when the overlay is visible and data is loaded
          ...(showAllCrimes && allCrimesData
            ? ['all-crimes-clusters', 'all-crimes-unclustered']
            : []),
        ]}
      >
        <NavigationControl position="top-right" />
        <GeolocateControl position="top-right" />

        {/* ── Route polylines ───────────────────────────────────────────── */}
        {[...routes].reverse().map((route, revIdx) => {
          const i = routes.length - 1 - revIdx
          const isSelected = i === selectedIdx
          return (
            <Source key={i} id={`route-${i}`} type="geojson" data={route.geometry}>
              {/* Invisible wide hit-area for click detection */}
              <Layer
                id={`route-${i}-hit`}
                type="line"
                paint={{ 'line-width': 24, 'line-opacity': 0 }}
              />
              {/* Halo effect — wider blurred layer under the selected route */}
              <Layer
                id={`route-${i}-halo`}
                type="line"
                paint={{
                  'line-color':   ROUTE_TYPE_COLOR[route.route_type] ?? '#6b7280',
                  'line-width':   isSelected ? 20 : 0,
                  'line-opacity': isSelected ? 0.18 : 0,
                  'line-blur':    10,
                }}
              />
              {/* Main route line */}
              <Layer
                id={`route-${i}-line`}
                type="line"
                paint={{
                  'line-color':   ROUTE_TYPE_COLOR[route.route_type] ?? '#6b7280',
                  'line-width':   isSelected ? 7 : 2.5,
                  'line-opacity': isSelected ? 1 : 0.35,
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
        {activeGeneralPopup !== null && generalIncidents[activeGeneralPopup] && (
          <Popup
            latitude={generalIncidents[activeGeneralPopup].lat}
            longitude={generalIncidents[activeGeneralPopup].lng}
            anchor="bottom"
            offset={14}
            onClose={() => setActiveGeneralPopup(null)}
            closeButton={false}
            closeOnClick={false}
            maxWidth="340px"
            className="crime-popup"
          >
            <IncidentPopupCard
              inc={generalIncidents[activeGeneralPopup]}
              onClose={() => setActiveGeneralPopup(null)}
              onGetDetails={inc => { setCrimeDetailInc(inc); setActiveGeneralPopup(null) }}
            />
          </Popup>
        )}

        {/* ── Personalised incident popup ───────────────────────────────── */}
        {activePersonalisedPopup !== null && personalisedDots[activePersonalisedPopup] && (
          <Popup
            latitude={personalisedDots[activePersonalisedPopup].lat}
            longitude={personalisedDots[activePersonalisedPopup].lng}
            anchor="bottom"
            offset={22}
            onClose={() => setActivePersonalisedPopup(null)}
            closeButton={false}
            closeOnClick={false}
            maxWidth="340px"
            className="crime-popup"
          >
            <IncidentPopupCard
              inc={personalisedDots[activePersonalisedPopup]}
              onClose={() => setActivePersonalisedPopup(null)}
              onGetDetails={inc => { setCrimeDetailInc(inc); setActivePersonalisedPopup(null) }}
            />
          </Popup>
        )}

        {/* ── All-crimes cluster layer ──────────────────────────────────── */}
        {showAllCrimes && allCrimesData && (
          <Source
            id="all-crimes-src"
            type="geojson"
            data={allCrimesData}
            cluster={true}
            clusterMaxZoom={14}
            clusterRadius={40}
          >
            {/* Cluster bubbles */}
            <Layer
              id="all-crimes-clusters"
              type="circle"
              filter={['has', 'point_count']}
              paint={{
                'circle-color': ['step', ['get', 'point_count'], '#f59e0b', 100, '#ef4444', 500, '#7c3aed'],
                'circle-radius': ['step', ['get', 'point_count'], 16, 100, 22, 500, 30],
                'circle-opacity': 0.85,
                'circle-stroke-width': 2,
                'circle-stroke-color': 'white',
              }}
            />
            {/* Cluster count label */}
            <Layer
              id="all-crimes-cluster-count"
              type="symbol"
              filter={['has', 'point_count']}
              layout={{ 'text-field': '{point_count_abbreviated}', 'text-size': 11 }}
              paint={{ 'text-color': 'white' }}
            />
            {/* Individual crime dots */}
            <Layer
              id="all-crimes-unclustered"
              type="circle"
              filter={['!', ['has', 'point_count']]}
              paint={{
                'circle-color':        CRIME_COLOR_EXPR,
                'circle-radius':       5,
                'circle-opacity':      0.85,
                'circle-stroke-width': 1.5,
                'circle-stroke-color': 'white',
              }}
            />
          </Source>
        )}

        {/* ── All-crimes dot popup ──────────────────────────────────────── */}
        {activeCrimePopup && (
          <Popup
            latitude={activeCrimePopup.lat}
            longitude={activeCrimePopup.lng}
            anchor="bottom"
            offset={14}
            onClose={() => setActiveCrimePopup(null)}
            closeButton={false}
            closeOnClick={false}
            maxWidth="340px"
            className="crime-popup"
          >
            <IncidentPopupCard
              inc={activeCrimePopup.inc}
              onClose={() => setActiveCrimePopup(null)}
              onGetDetails={inc => { setCrimeDetailInc(inc); setActiveCrimePopup(null) }}
            />
          </Popup>
        )}

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

      {/* ── Heatmap floating card (top-left) ─────────────────────────────── */}
      {mapReady && (
        <div className="absolute top-4 left-4 z-10 flex flex-col gap-2">
          <HeatmapControl
            showHeatmap={showHeatmap}
            onToggle={() => setShowHeatmap(v => !v)}
            activeCategory={activeCategory}
            onCategoryChange={setActiveCategory}
          />
          {/* All-crimes toggle */}
          <div className="bg-white rounded-2xl shadow-md border border-slate-200 overflow-hidden">
            <button
              onClick={handleToggleAllCrimes}
              disabled={allCrimesLoading}
              className="flex items-center justify-between gap-3 w-full px-4 py-3 hover:bg-slate-50 transition-colors duration-150 disabled:opacity-60"
            >
              <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full flex-shrink-0 transition-colors duration-200 ${showAllCrimes ? 'bg-rose-500' : 'bg-slate-300'}`} />
                <span className={`text-sm font-medium transition-colors duration-200 ${showAllCrimes ? 'text-slate-800' : 'text-slate-400'}`}>
                  {allCrimesLoading ? 'Loading…' : 'All Crime Points'}
                </span>
              </div>
              <div className={`relative w-9 h-5 rounded-full transition-colors duration-200 flex-shrink-0 ${showAllCrimes ? 'bg-rose-500' : 'bg-slate-200'}`}>
                <div className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${showAllCrimes ? 'translate-x-[17px]' : 'translate-x-0.5'}`} />
              </div>
            </button>
          </div>
        </div>
      )}

      {/* ── Risk legend (bottom-left) ─────────────────────────────────────── */}
      {showHeatmap && mapReady && (
        <div className="absolute bottom-6 left-4 z-10">
          <RiskLegend activeCat={activeCat} />
        </div>
      )}

      {/* ── Route type legend (bottom-right) ─────────────────────────────── */}
      {routes.length > 0 && mapReady && (
        <div className="absolute bottom-6 right-4 z-50">
          <RouteLegend routes={routes} />
        </div>
      )}

      {/* ── Crime detail panel ───────────────────────────────────────────── */}
      {/* Rendered outside <Map> so it sits above MapLibre's canvas and controls */}
      {crimeDetailInc && (
        <CrimeDetailPanel
          inc={crimeDetailInc}
          onClose={() => setCrimeDetailInc(null)}
        />
      )}
    </div>
  )
}
