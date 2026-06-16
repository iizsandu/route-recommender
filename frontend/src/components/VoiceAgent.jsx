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

// States: 'idle' | 'recording' | 'processing' | 'response'
export default function VoiceAgent() {
  const [status, setStatus]   = useState('idle')
  const [result, setResult]   = useState(null)   // { transcript, response }
  const [error, setError]     = useState(null)

  const streamRef   = useRef(null)   // MediaStream (audio + video tracks)
  const recorderRef = useRef(null)   // MediaRecorder (audio track only)
  const chunksRef   = useRef([])     // accumulated audio Blob chunks
  const videoRef    = useRef(null)   // <video> element for selfie preview

  // Stop all tracks when the component unmounts so the browser camera light turns off.
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
      // Request both audio and video so the user sees a selfie preview.
      // We only record audio — video is display-only.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: { facingMode: 'user' },
      })
      streamRef.current = stream

      // Attach stream to <video> for live preview (muted to prevent echo).
      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }

      // MediaRecorder on audio track only — no video bytes sent to backend.
      const audioOnly = new MediaStream(stream.getAudioTracks())
      const recorder  = new MediaRecorder(audioOnly, { mimeType: 'audio/webm' })
      recorderRef.current = recorder
      chunksRef.current   = []

      recorder.ondataavailable = e => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      recorder.onstop = async () => {
        stopStream()
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        setStatus('processing')

        const data = await queryAgent(blob)
        if (data) {
          setResult(data)
          setStatus('response')
        } else {
          setError('No response from safety assistant. Please try again.')
          setStatus('idle')
        }
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

  function handleButtonClick() {
    if (status === 'idle' || status === 'response') {
      setResult(null)
      startRecording()
    } else if (status === 'recording') {
      stopRecording()
    }
    // 'processing' — button is disabled, click does nothing
  }

  const buttonBg =
    status === 'recording'  ? 'bg-red-500 hover:bg-red-600' :
    status === 'processing' ? 'bg-slate-500 cursor-not-allowed' :
                              'bg-indigo-600 hover:bg-indigo-700 hover:scale-105'

  const label =
    status === 'idle'       ? 'Ask Safety AI' :
    status === 'recording'  ? 'Tap to stop'   :
    status === 'processing' ? 'Processing…'   : ''

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
          {/* REC badge */}
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
