// frontend/src/components/LocationInput.jsx
import { useEffect, useRef, useState } from 'react'
import { POPULAR_PLACES } from '../data/popularPlaces'

const MAX_SUGGESTIONS = 8

// Text input with a "popular places" autocomplete dropdown. Free text is
// still accepted and geocoded server-side as before; clicking a suggestion
// attaches its lat/lng so the caller can skip geocoding for that field.
export default function LocationInput({ value, onChange, placeholder, inputClassName }) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef(null)

  useEffect(() => {
    function handleClickOutside(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const query = value.trim().toLowerCase()
  const suggestions = (
    query
      ? POPULAR_PLACES.filter(p => p.name.toLowerCase().includes(query))
      : POPULAR_PLACES
  ).slice(0, MAX_SUGGESTIONS)

  function handleSelect(place) {
    onChange(place.name, place)
    setOpen(false)
  }

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={value}
        // WHY null place: any manual edit invalidates a previously selected
        // place's coordinates — falls back to geocoding the new text.
        onChange={e => onChange(e.target.value, null)}
        onFocus={() => setOpen(true)}
        onKeyDown={e => e.key === 'Escape' && setOpen(false)}
        placeholder={placeholder}
        required
        autoComplete="off"
        className={inputClassName}
      />
      {open && suggestions.length > 0 && (
        <div className="absolute left-0 right-0 top-full mt-1 z-30 max-h-56 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg py-1">
          {suggestions.map(place => (
            <button
              key={place.name}
              type="button"
              // WHY preventDefault: keeps the input focused through the click
              // instead of blurring first (avoids a focus flicker).
              onMouseDown={e => e.preventDefault()}
              onClick={() => handleSelect(place)}
              className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left text-sm text-slate-700 hover:bg-indigo-50 hover:text-indigo-700"
            >
              <span className="truncate">{place.name}</span>
              <span className="flex-shrink-0 text-xs text-slate-400">{place.area}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
