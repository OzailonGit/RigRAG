import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react'
import { ArrowUpIcon, StopIcon } from './icons'

const MAX_HEIGHT_PX = 220

export interface ComposerHandle {
  focus: () => void
  fill: (text: string) => void
}

interface Props {
  busy: boolean
  onSend: (text: string) => void
  onStop: () => void
}

export const Composer = forwardRef<ComposerHandle, Props>(function Composer({ busy, onSend, onStop }, ref) {
  const [text, setText] = useState('')
  const textarea = useRef<HTMLTextAreaElement>(null)

  useImperativeHandle(ref, () => ({
    focus: () => textarea.current?.focus(),
    fill: (value: string) => {
      setText(value)
      textarea.current?.focus()
    },
  }))

  // Grow with the content up to MAX_HEIGHT_PX, then scroll. Width changes
  // re-wrap the text (and placeholder), so resize on window resize too.
  useLayoutEffect(() => {
    const el = textarea.current
    if (!el) return
    const fit = () => {
      el.style.height = 'auto'
      el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`
    }
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [text])

  const canSend = text.trim().length > 0 && !busy

  function submit() {
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <label htmlFor="composer-input" className="visually-hidden">Message RigRAG</label>
      <textarea
        id="composer-input"
        ref={textarea}
        rows={1}
        value={text}
        placeholder="Describe the PC you want, e.g. budget, games, resolution"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // Enter sends; Shift+Enter adds a line. Ignore Enter while an IME is composing.
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      {busy ? (
        <button type="button" className="composer-send" onClick={onStop} aria-label="Stop waiting for the reply">
          <StopIcon />
        </button>
      ) : (
        <button type="submit" className="composer-send" disabled={!canSend} aria-label="Send message">
          <ArrowUpIcon />
        </button>
      )}
    </form>
  )
})
