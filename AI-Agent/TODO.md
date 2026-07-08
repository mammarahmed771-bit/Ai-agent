# Desktop AI Agent Upgrade TODO

## Backend (Flask)
- [ ] Create agent architecture under `agent/` (planner, router, executor, memory, conversation, registry)
- [ ] Create tool registry under `tools/` with independent tool modules (file, terminal, python, browser, screenshot, clipboard, memory, internet, image, project)
- [ ] Implement safe-root path sandbox with `AGENT_SAFE_ROOT` support
- [ ] Implement permission gate protocol (`action_request`) for dangerous operations
- [ ] Add/extend Flask endpoints:
  - [ ] `POST /agent/chat`
  - [ ] `POST /agent/allow_action`
- [ ] Integrate Gemini generation while preserving existing behavior

## Frontend
- [ ] Add action approval modal + UI hooks for `action_request`
- [ ] Add tool execution cards UI (status, output, progress)
- [ ] Add collapsible terminal output panel
- [ ] Add collapsible planner summary section
- [ ] Update `static/script.js` to call `/agent/chat` and render tool events
- [ ] Update `static/style.css` for new UI components (cards, modal, animations)

## Dependencies / Runtime
- [ ] Update `requirements.txt`
- [ ] Document Playwright install step (if needed)

## Testing
- [x] Backend scaffolding: agent architecture + basic tools + `/agent/chat` route integrated
- [ ] Update TODO: Frontend tool event UI + permission approvals
- [ ] Test safe directory listing

- [ ] Test file read
- [ ] Test file write (expect permission)
- [ ] Test terminal `dir` (expect permission)
- [ ] Test multi-step agent flow: generate small project in workspace (expect permission only for edits/runs)

