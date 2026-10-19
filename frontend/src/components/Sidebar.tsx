import { forwardRef } from 'react'
import type { Conversation } from '../conversations'
import type { ThemePreference } from '../theme'
import { CloseIcon, LogoMark, MonitorIcon, MoonIcon, PlusIcon, SunIcon, TrashIcon } from './icons'

const THEMES: { value: ThemePreference; label: string; Icon: typeof SunIcon }[] = [
  { value: 'light', label: 'Light theme', Icon: SunIcon },
  { value: 'dark', label: 'Dark theme', Icon: MoonIcon },
  { value: 'system', label: 'Match system theme', Icon: MonitorIcon },
]

interface Props {
  conversations: Conversation[]
  activeId: string | null
  open: boolean
  theme: ThemePreference
  deletedTitle: string | null
  onNew: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onUndoDelete: () => void
  onClose: () => void
  onTheme: (theme: ThemePreference) => void
}

/** The ref points at the close button, which receives focus when the mobile drawer opens. */
export const Sidebar = forwardRef<HTMLButtonElement, Props>(function Sidebar(
  { conversations, activeId, open, theme, deletedTitle, onNew, onSelect, onDelete, onUndoDelete, onClose, onTheme },
  closeRef,
) {
  return (
    <aside className={`sidebar${open ? ' sidebar--open' : ''}`} aria-label="Conversations">
      <div className="sidebar-head">
        <span className="brand">
          <LogoMark />
          <span className="brand-name">RigRAG</span>
        </span>
        <button ref={closeRef} type="button" className="icon-button sidebar-close" onClick={onClose} aria-label="Close sidebar">
          <CloseIcon />
        </button>
      </div>

      <button type="button" className="new-chat" onClick={onNew}>
        <PlusIcon /> New build
      </button>

      <nav className="history" aria-label="Previous builds">
        {conversations.length === 0 ? (
          <p className="history-empty">Your builds will show up here.</p>
        ) : (
          <ul>
            {conversations.map((c) => (
              <li key={c.id} className={`history-item${c.id === activeId ? ' history-item--active' : ''}`}>
                <button
                  type="button"
                  className="history-link"
                  onClick={() => onSelect(c.id)}
                  aria-current={c.id === activeId ? 'page' : undefined}
                >
                  {c.title}
                </button>
                <button
                  type="button"
                  className="icon-button icon-button--small history-delete"
                  onClick={() => onDelete(c.id)}
                  aria-label={`Delete "${c.title}"`}
                >
                  <TrashIcon width={16} height={16} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </nav>

      {deletedTitle && (
        <p className="undo-notice">
          <span>Build deleted.</span>
          <button type="button" onClick={onUndoDelete}>Undo</button>
        </p>
      )}

      <div className="theme-switch" role="group" aria-label="Theme">
        {THEMES.map(({ value, label, Icon }) => (
          <button
            key={value}
            type="button"
            aria-pressed={theme === value}
            aria-label={label}
            title={label}
            className="theme-option"
            onClick={() => onTheme(value)}
          >
            <Icon width={16} height={16} />
          </button>
        ))}
      </div>
    </aside>
  )
})
