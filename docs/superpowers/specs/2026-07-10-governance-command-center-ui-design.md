# Governance Command Center UI Design

## Goal

Turn the Governance workspace from a default-form page into a compact, legible command center for incident response and execution controls.

## Layout

- A top status strip shows open incident count, current token/cost usage, and active policy version.
- The body is a responsive two-column grid: incidents occupy the wider operational column and budget/policy controls occupy the narrower control column.
- Empty incidents render an intentional waiting-state card with a concise explanation, rather than a large blank box.
- On narrow screens the grid becomes one column.

## Visual System

Use the existing warm paper, ink, bronze and jade tokens from `App.css`. Cards use a thin bronze rule, modest radius, dense 16–24px padding and no browser-default controls. Severity and lifecycle state use small, high-contrast pill labels. Keep labels and actions in Chinese; use English only for stable technical tokens such as `P0` and `v1`.

## Components

`GovernancePanel` owns the status strip and policy/budget controls. `IncidentPanel` owns incident cards, status changes and actions. Shared CSS class names live in `App.css`; no generic global element selectors are added.

## Testing

Existing component tests continue to assert behavior. Add visual-structure assertions for the status strip, empty-state callout, and responsive panel class names. Run Vitest and the production build after the change.
