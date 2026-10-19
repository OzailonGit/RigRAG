import { useEffect, useState } from 'react'

export type ThemePreference = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'rigrag.theme'

function read(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    if (value === 'light' || value === 'dark' || value === 'system') return value
  } catch {
    // Storage blocked; fall back to the OS setting.
  }
  return 'system'
}

/** "system" removes data-theme so the prefers-color-scheme media query decides. */
export function useTheme() {
  const [theme, setTheme] = useState<ThemePreference>(read)

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // Preference just won't persist.
    }
  }, [theme])

  return [theme, setTheme] as const
}
