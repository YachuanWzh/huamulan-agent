# Reasoning Stream Design

## Goal

Return model-provided reasoning or thinking text to the frontend when the model API includes it, and show it in the chat UI. The application must not invent reasoning content when the model does not provide it.

## Backend Protocol

The streaming API adds a new SSE event:

```text
event: reasoning
data: {"content":"..."}
```

Existing `token`, `requires_approval`, `done`, and `error` events keep their current behavior.

Reasoning extraction reads only fields present on the streamed chat model chunk. The extractor checks common provider locations:

- `chunk.additional_kwargs.reasoning_content`
- `chunk.additional_kwargs.reasoning`
- `chunk.additional_kwargs.thinking`
- `chunk.response_metadata.reasoning_content`
- `chunk.response_metadata.reasoning`
- `chunk.response_metadata.thinking`

If none of those fields contain non-empty text, no `reasoning` event is emitted. Both `/api/chat/stream` and `/api/approve/stream` use the same extraction path.

## Frontend Behavior

The API client adds a `StreamReasoning` event type with `content: string`.

The chat state extends assistant messages with:

- `reasoning?: string`
- `reasoningStreaming?: boolean`
- `reasoningCollapsed?: boolean`

When reasoning content arrives, the current assistant message is created if needed and its reasoning text is appended. The reasoning card is visible while reasoning is streaming. Once the first answer token arrives, or the stream ends with `done` or `requires_approval`, the reasoning card is marked complete and collapses by default.

The user can click the reasoning card header to expand or collapse it after completion. Messages without reasoning render exactly as they do today.

## Error Handling

Malformed or empty reasoning fields are ignored. Streaming errors continue to use the existing `error` event path.

## Testing

Backend tests cover emitting `reasoning` SSE events from mocked stream chunks and preserving existing output when chunks have no reasoning fields.

Frontend tests cover:

- SSE parsing of `reasoning` events.
- `useChat` appending reasoning to the assistant message and collapsing it after answer streaming starts or completes.
- `MessageBubble` rendering a completed collapsed reasoning card that can be expanded by user click.
