import { useState, useRef, useEffect } from 'react'
import { queryAgent } from '../api/client'

function MicIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="white">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="white" strokeWidth="2"
        fill="none" strokeLinecap="round" />
      <line x1="12" y1="19" x2="12" y2="23" stroke="white"
        strokeWidth="2" strokeLinecap="round" />
      <line x1="8" y1="23" x2="16" y2="23" stroke="white"
        strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="white">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  )
}

function SpinnerIcon() {
  return (
    <svg className="animate-spin w-6 h-6 text-white" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10"
        stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  )
}

// States: 'idle' | 'recording' | 'recorded' | 'processing' | 'response'
export default function VoiceAgent() {
  const [status, setStatus] = useState('idle')
  const [result, setResult] = useState(null)   // { transcript, response }
  const [error, setError]   = useState(null)

  const streamRef   = useRef(null)
  const recorderRef = useRef(null)
  const chunksRef   = useRef([])
  const videoRef    = useRef(null)
  // WHY blobRef: we defer processing until the user explicitly clicks
  // "Get recommendation", so the blob must survive between onstop and the click.
  const blobRef     = useRef(null)

  useEffect(() => {
    return () => stopStream()
  }, [])

  function stopStream() {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
  }

  async function startRecording() {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: { facingMode: 'user' },
      })
      streamRef.current = stream

      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }

      const audioOnly = new MediaStream(stream.getAudioTracks())
      const recorder  = new MediaRecorder(audioOnly, { mimeType: 'audio/webm' })
      recorderRef.current = recorder
      chunksRef.current   = []

      recorder.ondataavailable = e => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      // WHY 'recorded' not 'processing': we now wait for explicit confirmation
      // before sending the audio. The blob is stored in blobRef so submitRecording()
      // can access it when the user clicks "Get recommendation".
      recorder.onstop = () => {
        stopStream()
        blobRef.current = new Blob(chunksRef.current, { type: 'audio/webm' })
        setStatus('recorded')
      }

      recorder.start()
      setStatus('recording')
    } catch {
      setError('Camera/microphone access denied. Allow permissions and try again.')
      setStatus('idle')
    }
  }

  function stopRecording() {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop()
    }
  }

  async function submitRecording() {
    if (!blobRef.current) return
    setStatus('processing')
    const data = await queryAgent(blobRef.current)
    blobRef.current = null
    if (data) {
      setResult(data)
      setStatus('response')
    } else {
      setError('No response from safety assistant. Please try again.')
      setStatus('idle')
    }
  }

  function handleButtonClick() {
    if (status === 'idle' || status === 'response' || status === 'recorded') {
      setResult(null)
      blobRef.current = null
      startRecording()
    } else if (status === 'recording') {
      stopRecording()
    }
    // 'processing' — button is disabled
  }

  const buttonBg =
    status === 'recording'  ? 'bg-red-500 hover:bg-red-600' :
    status === 'processing' ? 'bg-slate-500 cursor-not-allowed' :
                              'bg-indigo-600 hover:bg-indigo-700 hover:scale-105'

  const label =
    status === 'idle'       ? 'Ask Safety AI'  :
    status === 'recording'  ? 'Tap to stop'    :
    status === 'processing' ? 'Processing…'    :
    status === 'recorded'   ? 'Record again'   : ''

  return (
    <div className="absolute bottom-8 right-4 z-10 flex flex-col items-end gap-2">

      {/* ── Response card ───────────────────────────────────────────────── */}
      {status === 'response' && result && (
        <div className="bg-slate-900 text-white rounded-2xl p-4 w-72 shadow-2xl">
          <p className="text-slate-400 text-xs italic mb-2 leading-relaxed">
            "{result.transcript}"
          </p>
          <p className="text-white text-sm font-medium leading-relaxed">
            {result.response}
          </p>
          <button
            onClick={() => { setResult(null); setStatus('idle') }}
            className="mt-3 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            Ask another question →
          </button>
        </div>
      )}

      {/* ── "Voice recorded" confirmation popup ─────────────────────────── */}
      {status === 'recorded' && (
        <div className="bg-white rounded-2xl p-4 w-64 shadow-2xl border border-slate-100">
          <div className="flex items-center gap-2.5 mb-2">
            <div className="w-7 h-7 rounded-full bg-emerald-100 flex items-center justify-center flex-shrink-0">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                stroke="#10b981" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <p className="text-sm font-semibold text-slate-800">Voice recorded</p>
          </div>
          <p className="text-xs text-slate-400 mb-3 leading-relaxed">
            Tap below to get your safety recommendation.
          </p>
          <button
            onClick={submitRecording}
            className="w-full py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold transition-colors flex items-center justify-center gap-1.5"
          >
            Get recommendation
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="5" y1="12" x2="19" y2="12" />
              <polyline points="12 5 19 12 12 19" />
            </svg>
          </button>
        </div>
      )}

      {/* ── Error message ───────────────────────────────────────────────── */}
      {error && (
        <div className="bg-red-950 text-red-200 text-xs rounded-xl px-3 py-2 w-64 shadow-lg">
          {error}
        </div>
      )}

      {/* ── Selfie preview while recording ──────────────────────────────── */}
      {status === 'recording' && (
        <div className="relative w-24 h-20 rounded-xl overflow-hidden border-2 border-red-500 shadow-lg">
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className="w-full h-full object-cover scale-x-[-1]"
          />
          <div className="absolute top-1 left-1 flex items-center gap-1 bg-red-600 rounded px-1 py-0.5">
            <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
            <span className="text-white text-[9px] font-bold">REC</span>
          </div>
        </div>
      )}

      {/* ── Main mic button ─────────────────────────────────────────────── */}
      <button
        onClick={handleButtonClick}
        disabled={status === 'processing'}
        className={`w-14 h-14 rounded-full shadow-2xl flex items-center justify-center
          transition-all duration-200 ${buttonBg}
          ${status === 'recording' ? 'animate-pulse' : ''}`}
        title={label}
        aria-label={label}
      >
        {status === 'processing' ? <SpinnerIcon /> :
         status === 'recording'  ? <StopIcon />   : <MicIcon />}
      </button>

      {/* ── Label under button ──────────────────────────────────────────── */}
      {label && (
        <span className="text-xs text-slate-500 text-center select-none">
          {label}
        </span>
      )}
    </div>
  )
}
