import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../conversations'
import { BuildCard } from './BuildCard'
import { CheckIcon, CopyIcon, LogoMark, RetryIcon } from './icons'

// Local models can take a minute per reply, so say so instead of looking stuck.
const SLOW_AFTER_S = 8

function Thinking({ since }: { since: number }) {
  const [elapsed, setElapsed] = useState(() => Math.floor((Date.now() - since) / 1000))
  useEffect(() => {
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - since) / 1000)), 1000)
    return () => clearInterval(timer)
  }, [since])

  return (
    <div className="thinking">
      <span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>
      <span>
        {elapsed < SLOW_AFTER_S
          ? 'Working out your build'
          : `Still working (${elapsed}s). Local models can take up to a minute.`}
      </span>
    </div>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard blocked (insecure context or permissions); nothing to do.
    }
  }
  return (
    <button type="button" className="icon-button icon-button--small" onClick={copy} aria-label={copied ? 'Copied' : 'Copy reply'}>
      {copied ? <CheckIcon width={16} height={16} /> : <CopyIcon width={16} height={16} />}
    </button>
  )
}

interface Props {
  message: Message
  onRetry: () => void
}

export function MessageItem({ message, onRetry }: Props) {
  if (message.role === 'user') {
    return (
      <div className="message message--user" data-message-id={message.id}>
        <p className="message-bubble">{message.content}</p>
      </div>
    )
  }

  return (
    <div className="message message--assistant" data-message-id={message.id}>
      <div className="message-avatar" aria-hidden="true">
        <LogoMark width={20} height={20} />
      </div>
      <div className="message-body">
        {message.pending ? (
          <Thinking since={message.createdAt} />
        ) : message.error ? (
          <div className="message-error">
            <p>{message.error}</p>
            <button type="button" className="text-button" onClick={onRetry}>
              <RetryIcon width={16} height={16} /> Try again
            </button>
          </div>
        ) : (
          <>
            <div className="prose">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
            {message.build && <BuildCard result={message.build} />}
            <div className="message-actions">
              <CopyButton text={message.content} />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
