import { useState, useRef } from 'react'

interface Props {
  onSend: (text: string) => void
}

export function CommandBar({ onSend }: Props) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSubmit = () => {
    const text = value.trim()
    if (!text) return
    onSend(text)
    setValue('')
  }

  return (
    <div className="command-bar" onClick={() => inputRef.current?.focus()}>
      <span className="command-prompt">❯</span>
      <input
        ref={inputRef}
        className="command-input"
        placeholder="@agent message · #chain approve · /command"
        spellCheck={false}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') handleSubmit()
        }}
      />
      <div className="command-hints">
        <span>
          <kbd>Enter</kbd> send
        </span>
      </div>
    </div>
  )
}
