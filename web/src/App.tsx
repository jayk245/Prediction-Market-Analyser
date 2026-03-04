import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import { ScanDashboard } from './components/scan/ScanDashboard'
import { LiveDashboard } from './components/live/LiveDashboard'

function Nav() {
  const base = 'px-4 py-2 text-sm font-medium rounded transition-colors'
  const active = `${base} bg-gray-700 text-white`
  const inactive = `${base} text-gray-400 hover:text-white hover:bg-gray-800`
  return (
    <nav className="flex items-center justify-between px-5 py-3 bg-gray-900 border-b border-gray-800 shrink-0">
      <Link to="/" className="text-sm font-bold text-white tracking-tight">
        📊 PM Surveillance
      </Link>
      <div className="flex gap-1">
        <NavLink to="/"    end  className={({ isActive }) => isActive ? active : inactive}>Scan Reports</NavLink>
        <NavLink to="/live"     className={({ isActive }) => isActive ? active : inactive}>Live Alerts</NavLink>
      </div>
    </nav>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex flex-col min-h-screen">
        <Nav />
        <main className="flex-1">
          <Routes>
            <Route path="/"     element={<ScanDashboard />} />
            <Route path="/live" element={<LiveDashboard />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
