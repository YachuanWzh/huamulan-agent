import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { MessageBubble } from './MessageBubble'

describe('MessageBubble', () => {
  it('renders user message', () => {
    render(<MessageBubble role="user" content="Hello" />)
    expect(screen.getByText('Hello')).toBeInTheDocument()
    expect(screen.getByTestId('message-bubble')).toHaveClass('user')
  })

  it('renders assistant message', () => {
    render(<MessageBubble role="assistant" content="Hi there!" />)
    expect(screen.getByText('Hi there!')).toBeInTheDocument()
    expect(screen.getByTestId('message-bubble')).toHaveClass('assistant')
  })

  it('renders query-rewrite and skill-route cards without dumping raw JSON', async () => {
    render(
      <MessageBubble
        role="assistant"
        content="好的"
        cards={[
          {
            type: 'card', card_type: 'query_rewrite',
            rewritten_query: '查询系统的运行状况和指标', original_query: '我想看看这个系统',
            intent: 'general', secondary_intents: [], confidence: 0.3,
            needs_clarification: true, missing_slots: ['service_name'], sub_queries: [],
          },
          {
            type: 'card', card_type: 'skill_route',
            selected_skills: ['otel-query'], confidence: 0.9,
            reason: '适合此需求', stage: 'llm_judge',
          },
        ]}
      />,
    )
    expect(screen.getByText(/查询改写/)).toBeInTheDocument()
    expect(screen.getByText(/技能路由/)).toBeInTheDocument()
    expect(screen.queryByText(/rewritten_query/)).toBeNull()
    expect(screen.queryByText(/selectedSkill/)).toBeNull()

    await userEvent.click(screen.getByText(/技能路由/))
    expect(screen.getByText(/otel-query/)).toBeInTheDocument()
  })

  it('renders tool call with pending badge', () => {
    render(<MessageBubble role="tool_call" content="get_time" approvalStatus="pending" />)
    expect(screen.getByText('get_time')).toBeInTheDocument()
    expect(screen.getByText('待审批')).toBeInTheDocument()
  })

  it('renders tool call with approved badge', () => {
    render(<MessageBubble role="tool_call" content="get_time" approvalStatus="approved" />)
    expect(screen.getByText('已批准')).toBeInTheDocument()
  })

  it('renders tool call with denied badge', () => {
    render(<MessageBubble role="tool_call" content="get_time" approvalStatus="denied" />)
    expect(screen.getByText('已拒绝')).toBeInTheDocument()
  })

  it('collapses tool results by default and expands them into a scrollable panel', async () => {
    const user = userEvent.setup()
    const longToolOutput = Array.from({ length: 40 }, (_, index) => {
      return `C:\\idea\\langgraph-claw\\frontend\\src\\file-${index}.tsx`
    }).join('\n')

    render(<MessageBubble role="tool_call" content={longToolOutput} approvalStatus="approved" />)

    const toggle = screen.getByRole('button', { name: /tool_result/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(/file-39\.tsx/)).not.toBeInTheDocument()

    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('region', { name: /tool_result/i })).toHaveClass(
      'tool-result-content',
    )
    expect(screen.getByText(/file-39\.tsx/)).toBeInTheDocument()
  })

  it('renders role label', () => {
    render(<MessageBubble role="user" content="test" />)
    expect(screen.getByText('你')).toBeInTheDocument()
  })

  it('renders assistant role label', () => {
    render(<MessageBubble role="assistant" content="test" />)
    expect(screen.getByText('木兰')).toBeInTheDocument()
  })

  it('shows typewriter cursor when streaming', () => {
    render(<MessageBubble role="assistant" content="typing..." streaming={true} />)
    expect(screen.getByTestId('typewriter-cursor')).toBeInTheDocument()
    expect(screen.getAllByText('typing...').length).toBeGreaterThan(0)
  })

  it('does not show typewriter cursor when not streaming', () => {
    render(<MessageBubble role="assistant" content="done" />)
    expect(screen.queryByTestId('typewriter-cursor')).not.toBeInTheDocument()
  })

  it('does not show typewriter cursor when streaming is false', () => {
    render(<MessageBubble role="assistant" content="done" streaming={false} />)
    expect(screen.queryByTestId('typewriter-cursor')).not.toBeInTheDocument()
  })

  it('renders completed reasoning collapsed by default and expands on click', async () => {
    const user = userEvent.setup()
    let toggled = false
    render(
      <MessageBubble
        id="m1"
        role="assistant"
        content="Answer"
        reasoning="Hidden thought"
        reasoningCollapsed={true}
        onToggleReasoning={() => {
          toggled = true
        }}
      />,
    )

    expect(screen.getByRole('button', { name: /已完成/i })).toBeInTheDocument()
    expect(screen.queryByText('Hidden thought')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /已完成/i }))

    expect(toggled).toBe(true)
  })

  it('renders streaming reasoning expanded', () => {
    render(
      <MessageBubble
        id="m1"
        role="assistant"
        content=""
        reasoning="Working it out"
        reasoningStreaming={true}
        reasoningCollapsed={false}
        onToggleReasoning={() => {}}
      />,
    )

    expect(screen.getByText('Working it out')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /思考中/i })).toBeInTheDocument()
  })

  it('renders streaming compacting expanded', () => {
    render(
      <MessageBubble
        id="m1"
        role="assistant"
        content=""
        compacting="Compacting context"
        compactingStreaming={true}
        compactingCollapsed={false}
        onToggleCompacting={() => {}}
      />,
    )

    expect(screen.getByText('Compacting context')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /压缩上下文中/i })).toBeInTheDocument()
  })

  it('renders assistant message as markdown with bold and italic', () => {
    render(<MessageBubble role="assistant" content="**bold** and *italic*" />)

    const bold = screen.getByText('bold')
    expect(bold).toBeInTheDocument()
    expect(bold.tagName).toBe('STRONG')

    const italic = screen.getByText('italic')
    expect(italic).toBeInTheDocument()
    expect(italic.tagName).toBe('EM')
  })

  it('renders assistant message code block', () => {
    render(<MessageBubble role="assistant" content={'```js\nconst x = 1;\n```'} />)

    expect(screen.getByText(/const x = 1/)).toBeInTheDocument()
  })

  it('does not render user message as markdown', () => {
    render(<MessageBubble role="user" content="**not bold**" />)

    const text = screen.getByText('**not bold**')
    expect(text).toBeInTheDocument()
    // user messages should stay as plain text, not be parsed as markdown
    expect(text.tagName).not.toBe('STRONG')
  })

  it('renders tool call message as plain text (no markdown)', () => {
    render(<MessageBubble role="tool_call" content="**tool output**" approvalStatus="approved" />)

    expect(screen.getByText('**tool output**')).toBeInTheDocument()
  })

  it('shows typewriter cursor on assistant message when streaming', () => {
    const { container } = render(
      <MessageBubble role="assistant" content="typing..." streaming={true} />,
    )
    const cursor = container.querySelector('.typewriter-cursor')
    expect(cursor).toBeInTheDocument()
  })
})

it('marks rewrite card as unchanged when rewritten equals original', () => {
    render(
      <MessageBubble
        role="assistant"
        content="好的"
        cards={[{
          type: 'card', card_type: 'query_rewrite',
          rewritten_query: '排查 p99 延迟', original_query: '排查 p99 延迟',
          intent: 'troubleshoot', secondary_intents: [], confidence: 1.0,
          needs_clarification: false, missing_slots: [], sub_queries: [],
        }]}
      />,
    )
    // Click to expand the card so we can read the body label
    return userEvent.click(screen.getByText(/查询改写/)).then(() => {
      expect(screen.getByText(/未做改写/)).toBeInTheDocument()
    })
  })

describe('route-card / rewrite-card styling', () => {
  // Helper: read App.css source and pull out a CSS rule block for the given selector.
  function readCssRule(selector: string): string {
    const css = readFileSync(resolve(__dirname, '../App.css'), 'utf8')
    // Match e.g. ".route-card-key { ... }" — balanced braces are not required for
    // our small flat rules. Use a non-greedy match bounded by the next "}" at the
    // same indent level (newline + spaces + "}" terminates the block).
    const re = new RegExp(`\\${selector}\\s*\\{[^}]*\\}`, 'm')
    const m = css.match(re)
    return m ? m[0] : ''
  }

  it('route-card-key uses a dark, readable color (not light-alpha on light bg)', () => {
    const rule = readCssRule('.route-card-key')
    expect(rule).not.toBe('')
    // The app's parchment background is light; light-alpha text is invisible.
    // Project theme has --color-muted and --color-text — both dark.
    expect(rule).toMatch(/var\(--color-(text|muted|saddle-leather)\)/)
    expect(rule).not.toMatch(/rgba\(139,\s*148,\s*158/)
    expect(rule).not.toMatch(/rgba\(251,\s*252,\s*248/)
  })

  it('route-card-val uses a dark, readable color', () => {
    const rule = readCssRule('.route-card-val')
    expect(rule).not.toBe('')
    expect(rule).toMatch(/var\(--color-text\)/)
    expect(rule).not.toMatch(/rgba\(251,\s*252,\s*248,\s*0\.82/)
  })

  it('route-card-header text is readable (not light-alpha)', () => {
    const rule = readCssRule('.route-card-header')
    expect(rule).not.toBe('')
    expect(rule).toMatch(/var\(--color-(text|muted)\)/)
    expect(rule).not.toMatch(/rgba\(251,\s*252,\s*248,\s*0\.6\)/)
  })

  it('rewrite-text uses a dark color (visible on light parchment)', () => {
    const rule = readCssRule('.rewrite-text')
    expect(rule).not.toBe('')
    // rewrite-card sits on the user bubble (dark bg) → --color-user-text (#fffaf0) is correct
    expect(rule).toMatch(/var\(--color-(text|user-text)\)/)
    expect(rule).not.toMatch(/rgba\(160,\s*200,\s*255/)
  })
})
