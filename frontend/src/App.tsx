import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChatError, sendChatMessage } from './api'
import { Composer, type ComposerHandle } from './components/Composer'
import { EmptyState } from './components/EmptyState'
import { MenuIcon } from './components/icons'
import { MessageItem } from './components/MessageItem'
import { Sidebar } from './components/Sidebar'
import { makeId, useConversations, type Conversation, type Message } from './conversations'
import { requestChips } from './format'
import { useTheme } from './theme'

// Within this distance of the bottom, new content keeps the view pinned to it.
const STICK_TO_BOTTOM_PX = 120
const NO_MESSAGES: Message[] = []
const UNDO_WINDOW_MS = 6000

/** One short screen-reader message per reply state, instead of announcing whole build tables. */
function announcementFor(message: Message | undefined): string {
  if (!message || message.role !== 'assistant') return ''
  if (message.pending) return 'Working out your build.'
  if (message.error) return message.error
  return message.build ? 'Reply ready, with a recommended build.' : 'Reply ready.'
}

export default function App() {
  const { conversations, active, dispatch } = useConversations()
  const [theme, setTheme] = useTheme()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [deleted, setDeleted] = useState<{ conversation: Conversation; wasActive: boolean } | null>(null)
  const menuButton = useRef<HTMLButtonElement>(null)
  const sidebarClose = useRef<HTMLButtonElement>(null)
  const composer = useRef<ComposerHandle>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)
  const requests = useRef(new Map<string, AbortController>())

  const messages = active?.messages ?? NO_MESSAGES
  const busy = messages.some((m) => m.pending)
  const chips = requestChips(active?.state)

  // While waiting, keep the newest message in view. When a reply lands, scroll
  // to the question it answers instead of the bottom, so a long reply with a
  // build table is read from the top.
  const pendingIds = useRef(new Set<string>())
  useLayoutEffect(() => {
    const el = scroller.current
    const justResolved = [...pendingIds.current].some((id) => messages.some((m) => m.id === id && !m.pending))
    pendingIds.current = new Set(messages.filter((m) => m.pending).map((m) => m.id))
    if (!el || !pinned.current) return

    const lastUser = [...messages].reverse().find((m) => m.role === 'user')
    const anchor = justResolved && lastUser ? el.querySelector<HTMLElement>(`[data-message-id="${lastUser.id}"]`) : null
    el.scrollTop = anchor ? anchor.offsetTop - 16 : el.scrollHeight
  }, [messages])

  useEffect(() => {
    composer.current?.focus()
  }, [active?.id])

  useEffect(() => {
    if (!sidebarOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && closeSidebar()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sidebarOpen])

  useEffect(() => {
    if (!deleted) return
    const timer = setTimeout(() => setDeleted(null), UNDO_WINDOW_MS)
    return () => clearTimeout(timer)
  }, [deleted])

  function openSidebar() {
    setSidebarOpen(true)
    // The drawer is visibility:hidden until the class lands, so focus on the next frame.
    requestAnimationFrame(() => sidebarClose.current?.focus())
  }

  function closeSidebar() {
    setSidebarOpen(false)
    if (window.matchMedia('(max-width: 820px)').matches) menuButton.current?.focus()
  }

  function deleteConversation(id: string) {
    const conversation = conversations.find((c) => c.id === id)
    if (!conversation) return
    for (const m of conversation.messages) if (m.pending) requests.current.get(m.id)?.abort()
    dispatch({ type: 'delete', id })
    setDeleted({ conversation, wasActive: active?.id === id })
  }

  async function request(conversationId: string, sessionId: string, messageId: string, text: string) {
    const controller = new AbortController()
    requests.current.set(messageId, controller)
    try {
      const response = await sendChatMessage(sessionId, text, controller.signal)
      dispatch({
        type: 'resolve',
        conversationId,
        messageId,
        patch: { content: response.reply, build: response.build },
        state: response.state,
      })
    } catch (error) {
      const stopped = (error as Error).name === 'AbortError'
      const message = stopped
        ? 'Stopped. Send it again whenever you want.'
        : error instanceof ChatError
          ? error.message
          : 'Something went wrong while building this reply.'
      dispatch({ type: 'resolve', conversationId, messageId, patch: { error: message } })
    } finally {
      requests.current.delete(messageId)
    }
  }

  function send(text: string) {
    const conversationId = active?.id ?? makeId()
    const sessionId = active?.sessionId ?? makeId()
    const now = Date.now()
    const user: Message = { id: makeId(), role: 'user', content: text, createdAt: now }
    const placeholder: Message = { id: makeId(), role: 'assistant', content: '', pending: true, createdAt: now }
    pinned.current = true
    dispatch({ type: 'send', conversationId, sessionId, user, placeholder })
    void request(conversationId, sessionId, placeholder.id, text)
  }

  function retry(message: Message) {
    if (!active) return
    const index = active.messages.findIndex((m) => m.id === message.id)
    const prompt = active.messages.slice(0, index).reverse().find((m) => m.role === 'user')
    if (!prompt) return
    dispatch({ type: 'retry', conversationId: active.id, messageId: message.id })
    void request(active.id, active.sessionId, message.id, prompt.content)
  }

  function stop() {
    for (const m of messages) if (m.pending) requests.current.get(m.id)?.abort()
  }

  return (
    <div className="app">
      <Sidebar
        ref={sidebarClose}
        conversations={conversations}
        activeId={active?.id ?? null}
        open={sidebarOpen}
        theme={theme}
        deletedTitle={deleted?.conversation.title ?? null}
        onNew={() => {
          dispatch({ type: 'new' })
          setSidebarOpen(false)
        }}
        onSelect={(id) => {
          dispatch({ type: 'select', id })
          setSidebarOpen(false)
        }}
        onDelete={deleteConversation}
        onUndoDelete={() => {
          if (deleted) dispatch({ type: 'restore', conversation: deleted.conversation, select: deleted.wasActive })
          setDeleted(null)
        }}
        onClose={closeSidebar}
        onTheme={setTheme}
      />
      {sidebarOpen && <div className="scrim" onClick={closeSidebar} aria-hidden="true" />}

      <main className="chat">
        <header className="chat-header">
          <button ref={menuButton} type="button" className="icon-button menu-button" onClick={openSidebar} aria-label="Open sidebar">
            <MenuIcon />
          </button>
          <h1 className="chat-title">{active?.title ?? 'New build'}</h1>
          {chips.length > 0 && (
            <ul className="chips" aria-label="What RigRAG knows about this build">
              {chips.map((chip) => <li key={chip} className="chip">{chip}</li>)}
            </ul>
          )}
        </header>

        <div
          className="chat-scroll"
          ref={scroller}
          onScroll={(e) => {
            const el = e.currentTarget
            pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_TO_BOTTOM_PX
          }}
        >
          <div className="chat-column">
            {messages.length === 0 ? (
              <EmptyState onPick={(prompt) => composer.current?.fill(prompt)} />
            ) : (
              <div className="messages">
                {messages.map((m) => <MessageItem key={m.id} message={m} onRetry={() => retry(m)} />)}
              </div>
            )}
          </div>
        </div>
        <p className="visually-hidden" role="status">{announcementFor(messages[messages.length - 1])}</p>

        <div className="chat-footer">
          <div className="chat-column">
            <Composer ref={composer} busy={busy} onSend={send} onStop={stop} />
            <p className="disclaimer">
              Parts and prices come from RigRAG's catalog. Check compatibility notes before you buy.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
