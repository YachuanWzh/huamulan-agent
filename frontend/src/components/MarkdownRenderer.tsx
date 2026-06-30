import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  content: string
  streaming?: boolean
}

export function MarkdownRenderer({ content, streaming }: Props) {
  return (
    <div className="markdown-content" data-testid="markdown-renderer">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
      {streaming && <span className="typewriter-cursor" data-testid="typewriter-cursor" />}
    </div>
  )
}
