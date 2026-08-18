import React from 'react'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard.jsx'
import About from './pages/About.jsx'
import Research from './pages/Research.jsx'
import Breakdown from './pages/Breakdown.jsx'
import Architecture from './pages/Architecture.jsx'

function Nav() {
  const dagsterUrl = `//${window.location.hostname}:3000`
  return (
    <header className="topbar">
      <h1>Mineral prospectivity</h1>
      <nav className="nav">
        <NavLink to="/" end>Map</NavLink>
        <NavLink to="/breakdown">Breakdown</NavLink>
        <NavLink to="/research">Research</NavLink>
        <NavLink to="/architecture">Architecture</NavLink>
        <NavLink to="/about">About</NavLink>
        <a href={dagsterUrl} target="_blank" rel="noreferrer" className="ext">
          Dagster ↗
        </a>
      </nav>
    </header>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <Nav />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/breakdown" element={<Breakdown />} />
          <Route path="/research" element={<Research />} />
          <Route path="/architecture" element={<Architecture />} />
          <Route path="/about" element={<About />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
