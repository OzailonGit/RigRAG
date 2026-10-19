import { useEffect, useReducer } from 'react'
import type { BuildRequest, BuildResult } from './api'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  build?: BuildResult | null
  /** Set on an assistant placeholder whose request failed. */
  error?: string
  pending?: boolean
  createdAt: number
}

export interface Conversation {
  id: string
  /** Backend chat session; the server keeps build context per session. */
  sessionId: string
  title: string
  messages: Message[]
  state?: BuildRequest
  updatedAt: number
}

interface Store {
  conversations: Conversation[]
  activeId: string | null
}

type Action =
  | { type: 'new' }
  | { type: 'select'; id: string }
  | { type: 'delete'; id: string }
  | { type: 'restore'; conversation: Conversation; select: boolean }
  | { type: 'send'; conversationId: string; sessionId: string; user: Message; placeholder: Message }
  | { type: 'resolve'; conversationId: string; messageId: string; patch: Partial<Message>; state?: BuildRequest }
  | { type: 'retry'; conversationId: string; messageId: string }

const STORAGE_KEY = 'rigrag.conversations.v1'
const TITLE_LENGTH = 48

export function makeId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function titleFrom(text: string): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > TITLE_LENGTH ? `${clean.slice(0, TITLE_LENGTH - 1)}…` : clean
}

function load(): Store {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Store
      // A request in flight when the page closed will never resolve.
      const conversations = parsed.conversations.map((c) => ({
        ...c,
        messages: c.messages.map((m) =>
          m.pending ? { ...m, pending: false, error: 'This reply was interrupted. Send the message again.' } : m,
        ),
      }))
      return { conversations, activeId: parsed.activeId }
    }
  } catch {
    // Storage blocked or corrupt; start fresh.
  }
  return { conversations: [], activeId: null }
}

function updateConversation(store: Store, id: string, update: (c: Conversation) => Conversation): Store {
  return { ...store, conversations: store.conversations.map((c) => (c.id === id ? update(c) : c)) }
}

function reducer(store: Store, action: Action): Store {
  switch (action.type) {
    case 'new':
      return { ...store, activeId: null }
    case 'select':
      return { ...store, activeId: action.id }
    case 'delete': {
      const conversations = store.conversations.filter((c) => c.id !== action.id)
      return { conversations, activeId: store.activeId === action.id ? null : store.activeId }
    }
    case 'restore': {
      if (store.conversations.some((c) => c.id === action.conversation.id)) return store
      const conversations = [...store.conversations, action.conversation].sort((a, b) => b.updatedAt - a.updatedAt)
      return { conversations, activeId: action.select ? action.conversation.id : store.activeId }
    }
    case 'send': {
      const exists = store.conversations.some((c) => c.id === action.conversationId)
      const now = Date.now()
      if (!exists) {
        const conversation: Conversation = {
          id: action.conversationId,
          sessionId: action.sessionId,
          title: titleFrom(action.user.content),
          messages: [action.user, action.placeholder],
          updatedAt: now,
        }
        return { conversations: [conversation, ...store.conversations], activeId: conversation.id }
      }
      const next = updateConversation(store, action.conversationId, (c) => ({
        ...c,
        messages: [...c.messages, action.user, action.placeholder],
        updatedAt: now,
      }))
      // Most recently used conversations sort first.
      next.conversations.sort((a, b) => b.updatedAt - a.updatedAt)
      return next
    }
    case 'resolve':
      return updateConversation(store, action.conversationId, (c) => ({
        ...c,
        state: action.state ?? c.state,
        messages: c.messages.map((m) => (m.id === action.messageId ? { ...m, ...action.patch, pending: false } : m)),
      }))
    case 'retry':
      return updateConversation(store, action.conversationId, (c) => ({
        ...c,
        messages: c.messages.map((m) => (m.id === action.messageId ? { ...m, error: undefined, pending: true } : m)),
      }))
  }
}

export function useConversations() {
  const [store, dispatch] = useReducer(reducer, undefined, load)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
    } catch {
      // Quota exceeded or storage blocked; the session still works in memory.
    }
  }, [store])

  const active = store.conversations.find((c) => c.id === store.activeId) ?? null
  return { conversations: store.conversations, active, dispatch }
}
