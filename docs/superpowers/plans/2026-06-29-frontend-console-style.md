# Frontend Console Style Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the frontend into a minimal LangGraph assistant console without reducing chat, history, checkpoint, skills, approval, or audit functionality.

**Architecture:** Keep React state, API calls, and component responsibilities intact. Add only semantic shell hooks where needed for layout and accessibility, and place the visual redesign in `frontend/src/App.css`.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, Testing Library, plain CSS.

## Global Constraints

- Do not change backend behavior or frontend API contracts.
- Do not remove existing controls: send, new conversation, skills reload, history open/delete/clear, checkpoint replay/delete/clear, audit refresh, approval approve/deny.
- Preserve a minimal visual direction: quiet palette, clear hierarchy, restrained motion.
- Follow TDD: write a failing test before implementation and verify it fails for the expected reason.

---

### Task 1: Console Shell Semantics

**Files:**
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

**Interfaces:**
- Consumes: existing `App`, `Sidebar`, and mocked tests.
- Produces: accessible shell names that CSS can style without changing business logic.

- [ ] **Step 1: Write the failing test**

```tsx
it('exposes the redesigned console shell without changing core controls', () => {
  render(<App />)

  expect(screen.getByRole('banner', { name: /assistant console/i })).toBeInTheDocument()
  expect(screen.getByRole('main', { name: /conversation workspace/i })).toBeInTheDocument()
  expect(screen.getByTestId('sidebar-shell')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /langgraph assistant/i })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/App.test.tsx --runInBand`

Expected: FAIL because the header/main labels and sidebar shell marker are not present yet.

- [ ] **Step 3: Write minimal implementation**

Add `aria-label="Assistant console"` to the header, `aria-label="Conversation workspace"` to the main element, and `data-testid="sidebar-shell"` to the sidebar root.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- src/App.test.tsx --runInBand`

Expected: PASS for `App.test.tsx`.

### Task 2: Minimal Console Visual Redesign

**Files:**
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: existing class names in `App.tsx`, `ChatPanel.tsx`, `MessageList.tsx`, `MessageBubble.tsx`, `MessageInput.tsx`, `Sidebar.tsx`, and `ToolApprovalCard.tsx`.
- Produces: a CSS-only visual redesign with no logic changes.

- [ ] **Step 1: Write the failing guard**

Use the Task 1 test as the guard for shell structure and the existing component tests as behavioral guards.

- [ ] **Step 2: Implement CSS redesign**

Revise tokens, app shell, chat surface, message bubbles, input bar, tabs, history rows, checkpoint cards, approval cards, and audit states. Keep all existing selectors that tests and components rely on.

- [ ] **Step 3: Run focused tests**

Run: `npm test -- src/App.test.tsx src/components/Sidebar.test.tsx src/components/ChatPanel.test.tsx`

Expected: PASS, confirming the redesigned shell did not remove key controls.

- [ ] **Step 4: Run build**

Run: `npm run build`

Expected: PASS, confirming TypeScript and Vite compilation.
