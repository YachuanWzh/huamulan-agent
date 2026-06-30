import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MarkdownRenderer } from './MarkdownRenderer'

describe('MarkdownRenderer', () => {
  it('renders plain text', () => {
    render(<MarkdownRenderer content="Hello world" />)
    expect(screen.getByText('Hello world')).toBeInTheDocument()
  })

  it('renders bold text', () => {
    render(<MarkdownRenderer content="**bold** text" />)
    const element = screen.getByText('bold')
    expect(element).toBeInTheDocument()
    expect(element.tagName).toBe('STRONG')
  })

  it('renders italic text', () => {
    render(<MarkdownRenderer content="*italic* text" />)
    const element = screen.getByText('italic')
    expect(element).toBeInTheDocument()
    expect(element.tagName).toBe('EM')
  })

  it('renders inline code', () => {
    render(<MarkdownRenderer content="use `const x = 1` here" />)
    const element = screen.getByText('const x = 1')
    expect(element).toBeInTheDocument()
    expect(element.tagName).toBe('CODE')
  })

  it('renders code blocks', () => {
    render(<MarkdownRenderer content={'```js\nconsole.log("hi")\n```'} />)
    expect(screen.getByText(/console\.log/)).toBeInTheDocument()
  })

  it('renders unordered lists', () => {
    // GFM requires a blank line before a list starts
    render(<MarkdownRenderer content={'\n- Item 1\n- Item 2'} />)
    expect(screen.getByText('Item 1')).toBeInTheDocument()
    expect(screen.getByText('Item 2')).toBeInTheDocument()
  })

  it('renders ordered lists', () => {
    render(<MarkdownRenderer content={'\n1. First\n2. Second'} />)
    expect(screen.getByText('First')).toBeInTheDocument()
    expect(screen.getByText('Second')).toBeInTheDocument()
  })

  it('renders headings', () => {
    render(<MarkdownRenderer content="## Heading Two" />)
    const heading = screen.getByText('Heading Two')
    expect(heading).toBeInTheDocument()
    expect(heading.tagName).toBe('H2')
  })

  it('renders links with security attributes', () => {
    render(<MarkdownRenderer content="[click here](https://example.com)" />)
    const link = screen.getByText('click here')
    expect(link).toBeInTheDocument()
    expect(link.tagName).toBe('A')
    expect(link).toHaveAttribute('href', 'https://example.com')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('renders blockquotes', () => {
    render(<MarkdownRenderer content="> quoted text" />)
    expect(screen.getByText(/quoted text/)).toBeInTheDocument()
  })

  it('renders tables (GFM)', () => {
    const md = [
      '| Col A | Col B |',
      '|-------|-------|',
      '| a1    | b1    |',
    ].join('\n')
    render(<MarkdownRenderer content={md} />)
    expect(screen.getByText('Col A')).toBeInTheDocument()
    expect(screen.getByText('a1')).toBeInTheDocument()
    expect(screen.getByText('b1')).toBeInTheDocument()
  })

  it('shows typewriter cursor when streaming', () => {
    const { container } = render(<MarkdownRenderer content="typing..." streaming={true} />)
    const cursor = container.querySelector('.typewriter-cursor')
    expect(cursor).toBeInTheDocument()
  })

  it('does not show typewriter cursor when not streaming', () => {
    const { container } = render(<MarkdownRenderer content="done" streaming={false} />)
    const cursor = container.querySelector('.typewriter-cursor')
    expect(cursor).toBeNull()
  })

  it('renders empty content without crashing', () => {
    render(<MarkdownRenderer content="" />)
    expect(screen.getByTestId('markdown-renderer')).toBeInTheDocument()
  })

  it('renders strikethrough (GFM)', () => {
    render(<MarkdownRenderer content="~~removed~~ text" />)
    const element = screen.getByText('removed')
    expect(element).toBeInTheDocument()
    expect(element.tagName).toBe('DEL')
  })
})
