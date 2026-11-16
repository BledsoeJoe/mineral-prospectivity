// What this deployment can do, as reported by /api/health. Null until the
// answer arrives; on failure every capability reads false, so controls this
// node cannot serve are never offered.
import React, { createContext, useContext, useEffect, useState } from 'react'
import { fetchHealth } from './api.js'

const CapabilitiesContext = createContext(null)

export function CapabilitiesProvider({ children }) {
  const [caps, setCaps] = useState(null)
  useEffect(() => {
    fetchHealth().then((h) => setCaps(h.capabilities ?? {})).catch(() => setCaps({}))
  }, [])
  return <CapabilitiesContext.Provider value={caps}>{children}</CapabilitiesContext.Provider>
}

export const useCapabilities = () => useContext(CapabilitiesContext)
