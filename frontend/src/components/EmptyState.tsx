const SUGGESTIONS = [
  { title: '1440p gaming, ₱70k', prompt: 'Build me a ₱70,000 gaming PC for 1440p, AMD CPU, and I need Wi-Fi.' },
  { title: 'Quiet coding workstation', prompt: 'I need a quiet ₱50,000 PC for programming and lots of browser tabs.' },
  { title: 'Esports on a budget', prompt: 'What is the best 1080p esports build under ₱40,000?' },
  { title: 'Video editing rig', prompt: 'Recommend a video editing PC around ₱120,000 with at least 32GB of RAM.' },
]

export function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="empty">
      <h2 className="empty-title">What are you building?</h2>
      <p className="empty-lede">
        Tell me your budget and what you'll use it for. Every part comes from a real catalog and is checked for
        compatibility before you see it.
      </p>
      <ul className="suggestions">
        {SUGGESTIONS.map((s) => (
          <li key={s.title}>
            <button type="button" className="suggestion" onClick={() => onPick(s.prompt)}>
              <span className="suggestion-title">{s.title}</span>
              <span className="suggestion-prompt">{s.prompt}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
