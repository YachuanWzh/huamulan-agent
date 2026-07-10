import { useState } from 'react'

/** Trigger a client-side download of a text string as a file. */
export function downloadText(text: string, filename: string) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const DEFAULT_LIMIT = 20000

interface TruncatedTextProps {
  text: string
  /** Max characters to render inline before truncating. */
  limit?: number
  /** Filename used when the user downloads the full content. */
  downloadName?: string
}

/**
 * Renders text but caps how much is placed in the DOM. Huge strings (multi-MB
 * tool results / checkpoint dumps) otherwise force the browser to lay out every
 * character, freezing the main thread. Returns a fragment so it can live inside
 * any wrapper (<pre>, <div>). Controls are inline-level to stay valid there.
 */
export function TruncatedText({ text, limit = DEFAULT_LIMIT, downloadName = 'content.txt' }: TruncatedTextProps) {
  const [full, setFull] = useState(false)
  const isLong = text.length > limit
  const shown = full || !isLong ? text : text.slice(0, limit)
  return (
    <>
      {shown}
      {isLong && (
        <span className="truncated-controls">
          {!full && (
            <span className="truncated-note">
              内容较长已截断（共 {text.length.toLocaleString()} 字符）
            </span>
          )}
          {!full && (
            <button type="button" onClick={() => setFull(true)}>
              显示全部
            </button>
          )}
          <button type="button" onClick={() => downloadText(text, downloadName)}>
            下载全文
          </button>
        </span>
      )}
    </>
  )
}
