# huamulan-agent Brand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand the app and README to `huamulan-agent` with a Hua Mulan-inspired frontend identity and proposal-only extension roadmap.

**Architecture:** Keep the existing React/FastAPI/LangGraph architecture unchanged. Limit code changes to visible React copy, CSS tokens, tests, and top-level README documentation.

**Tech Stack:** React 19, TypeScript 6, Vite 8, Vitest 4, Markdown.

## Global Constraints

- Extension items are proposal-only; do not add backend skills, plugins, APIs, or new runtime dependencies.
- Preserve existing console layout and core controls.
- Use the approved Mulan visual direction: Iron Armor, Jujube Red, Saddle Leather, Rice Paper, Bronze Green.
- Follow TDD: write failing tests before implementation.

---

### Task 1: Frontend Name And Signature Rail

**Files:**
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: existing `App` component.
- Produces: visible heading `huamulan-agent` and header rail text containing `东市 骏马`, `西市 鞍鞯`, `南市 辔头`, `北市 长鞭`.

- [ ] **Step 1: Write the failing test**

```tsx
it('presents the huamulan-agent identity and four-market kit rail', () => {
  render(<App />)

  expect(
    screen.getByRole('heading', { name: /huamulan-agent/i }),
  ).toBeInTheDocument()
  expect(screen.getByText(/东市\s*骏马/)).toBeInTheDocument()
  expect(screen.getByText(/西市\s*鞍鞯/)).toBeInTheDocument()
  expect(screen.getByText(/南市\s*辔头/)).toBeInTheDocument()
  expect(screen.getByText(/北市\s*长鞭/)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npm test -- App.test.tsx`
Expected: FAIL because `huamulan-agent` and the rail are not rendered yet.

- [ ] **Step 3: Write minimal implementation**

Update `App.tsx` header heading and add a compact header rail beside the thread info.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npm test -- App.test.tsx`
Expected: PASS.

### Task 2: Mulan Console Copy And CSS Tokens

**Files:**
- Modify: `frontend/src/AppCss.test.ts`
- Modify: `frontend/src/components/MessageInput.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: current class names and component structure.
- Produces: Mulan palette CSS variables and copy changes for new conversation, placeholder, send, skills/history/checkpoint/audit panels.

- [ ] **Step 1: Write failing CSS/copy tests**

Add assertions for palette variables such as `--color-iron-armor`, `--color-jujube-red`, `--color-saddle-leather`, `--color-rice-paper`, `--color-bronze-green`, and component tests where existing coverage is present.

- [ ] **Step 2: Run tests to verify failure**

Run: `cd frontend; npm test -- AppCss.test.ts MessageInput.test.tsx Sidebar.test.tsx`
Expected: FAIL because new palette tokens and copy are absent.

- [ ] **Step 3: Implement minimal UI copy and CSS**

Update visible labels while preserving button semantics and existing layout. Update CSS variables and affected selectors to use the approved palette.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd frontend; npm test -- AppCss.test.ts MessageInput.test.tsx Sidebar.test.tsx`
Expected: PASS.

### Task 3: README Reframe And Proposal Roadmap

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: existing README architecture content.
- Produces: top-level `huamulan-agent` description and proposal-only extension roadmap.

- [ ] **Step 1: Write a failing documentation check**

Run: `Select-String -Path README.md -Pattern "huamulan-agent","扩展项方案","东市买骏马","仅方案"`
Expected: FAIL or incomplete matches before the README update.

- [ ] **Step 2: Implement README changes**

Update title, intro, and add a roadmap section that states extension items are proposals only.

- [ ] **Step 3: Run documentation check**

Run: `Select-String -Path README.md -Pattern "huamulan-agent","扩展项方案","东市买骏马","仅方案"`
Expected: all patterns are present.

### Task 4: Final Verification

**Files:**
- No direct edits.

**Interfaces:**
- Consumes: all prior changes.
- Produces: verified frontend tests/build and documentation check output.

- [ ] **Step 1: Run frontend tests**

Run: `cd frontend; npm test`
Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run: `cd frontend; npm run build`
Expected: PASS.

- [ ] **Step 3: Inspect git diff**

Run: `git diff -- README.md frontend/src/App.tsx frontend/src/App.css frontend/src/App.test.tsx frontend/src/AppCss.test.ts frontend/src/components/MessageInput.tsx frontend/src/components/Sidebar.tsx`
Expected: changes are scoped to the approved brand/docs work.
