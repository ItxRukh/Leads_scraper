import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import '@fontsource-variable/inter'
import '@tabler/icons-webfont/dist/tabler-icons.min.css'
import './theme.css'
import './index.css'
import { enableThemeTransitions } from './theme.js'
import { installAuthFetch } from './auth.js'
import './pages/Login.css'
import App from './App.jsx'

enableThemeTransitions()
installAuthFetch()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
)
