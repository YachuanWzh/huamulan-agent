import { useState, type KeyboardEvent } from 'react'

interface Props {
  onSend: (message: string) => void
  onNewConversation?: () => void
  disabled?: boolean
}

export function MessageInput({ onSend, onNewConversation, disabled }: Props) {
  const [text, setText] = useState('')

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed) return
    onSend(trimmed)
    setText('')
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="message-input">
      <button
        className="new-conversation-button"
        onClick={onNewConversation}
        disabled={disabled}
        type="button"
      >
        New Conversation
      </button>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Type your message..."
        disabled={disabled}
        rows={1}
      />
      <button onClick={handleSend} disabled={disabled || !text.trim()} type="button">
        Send
      </button>
    </div>
  )
}
