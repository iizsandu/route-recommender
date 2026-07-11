import { useState, useRef, useEffect } from 'react'
import { queryAgentText } from '../api/client'

const SAMPLE_QUESTIONS = [
  'Is it safe to travel to Connaught Place tonight?',
  'Safest route from Lajpat Nagar to Saket?',
  'Recent incidents near Karol Bagh?',
  'How safe is Hauz Khas Village after 9 PM?',
  'Most common crimes in South Delhi?',
]

function ChatBubbleIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="white">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  )
}

function SendIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" fill="white" stroke="none" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  )
}

export default function ChatAgent() {
  const [open, setOpen]       = useState(false)
  const [messages, setMessages] = useState([])   // [{role:'user'|'bot', text:string}]
  const [input, setInput]     = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef             = useRef(null)
  const inputRef              = useRef(null)

  // Scroll to latest message whenever messages update or panel opens.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Focus input when panel opens.
  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  async function send(text) {
    const q = (text ?? input).trim()
    if (!q || loading) return
    setMessages(prev => [...prev, { role: 'user', text: q }])
    setInput('')
    setLoading(true)
    const data = await queryAgentText(q)
    setMessages(prev => [
      ...prev,
      { role: 'bot', text: data?.response || 'Sorry, no response from the safety assistant.' },
    ])
    setLoading(false)
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const isEmpty = messages.length === 0

  return (
    <div className="absolute bottom-8 right-20 z-40 flex flex-col items-end gap-2">

      {/* ── Chat panel ──────────────────────────────────────────────────── */}
      {open && (
        <div className="bg-white rounded-2xl shadow-2xl border border-slate-100 w-80 flex flex-col overflow-hidden"
          style={{ maxHeight: '440px' }}>

          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 bg-indigo-600 flex-shrink-0">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <p className="text-white text-sm font-semibold">Safety Assistant</p>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="text-white/60 hover:text-white transition-colors"
              aria-label="Close chat"
            >
              <CloseIcon />
            </button>
          </div>

          {/* Messages + sample chips */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2 min-h-0">

            {/* Empty state: show sample question chips */}
            {isEmpty && (
              <div>
                <p className="text-xs text-slate-400 mb-2.5 px-0.5">Try asking:</p>
                <div className="flex flex-col gap-1.5">
                  {SAMPLE_QUESTIONS.map(q => (
                    <button
                      key={q}
                      onClick={() => send(q)}
                      className="text-xs text-left bg-slate-50 hover:bg-indigo-50 text-slate-600 hover:text-indigo-700 px-3 py-2 rounded-xl border border-slate-200 hover:border-indigo-200 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Message bubbles */}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`text-xs leading-relaxed rounded-2xl px-3 py-2 max-w-[85%] ${
                  m.role === 'user'
                    ? 'bg-indigo-600 text-white rounded-br-sm'
                    : 'bg-slate-100 text-slate-700 rounded-bl-sm'
                }`}>
                  {m.text}
                </div>
              </div>
            ))}

            {/* Typing indicator */}
            {loading && (
              <div className="flex justify-start">
                <div className="bg-slate-100 rounded-2xl rounded-bl-sm px-3 py-2.5 flex gap-1 items-center">
                  {[0, 150, 300].map(delay => (
                    <span
                      key={delay}
                      className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce"
                      style={{ animationDelay: `${delay}ms` }}
                    />
                  ))}
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input row */}
          <div className="border-t border-slate-100 p-2.5 flex gap-2 flex-shrink-0">
            <input
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about safety in Delhi…"
              disabled={loading}
              className="flex-1 text-xs bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 outline-none focus:border-indigo-300 focus:ring-1 focus:ring-indigo-100 disabled:opacity-50 transition-all"
            />
            <button
              onClick={() => send()}
              disabled={!input.trim() || loading}
              className="w-8 h-8 flex items-center justify-center bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-200 rounded-xl transition-colors flex-shrink-0"
              aria-label="Send"
            >
              <SendIcon />
            </button>
          </div>
        </div>
      )}

      {/* ── Toggle button ────────────────────────────────────────────────── */}
      <button
        onClick={() => setOpen(o => !o)}
        className={`w-14 h-14 rounded-full shadow-2xl flex items-center justify-center
          transition-all duration-200
          ${open
            ? 'bg-indigo-700'
            : 'bg-indigo-600 hover:bg-indigo-700 hover:scale-105'}`}
        aria-label={open ? 'Close chat' : 'Open safety chat'}
      >
        <ChatBubbleIcon />
      </button>

      <span className="text-xs text-slate-500 text-center select-none">Ask AI</span>
    </div>
  )
}
