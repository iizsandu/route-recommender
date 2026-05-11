import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles/index.css'

// WHY: React 18 replaces ReactDOM.render with createRoot — this enables
// concurrent rendering features (transitions, Suspense boundaries, etc.)
// StrictMode double-invokes renders in dev to surface side-effect bugs
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
