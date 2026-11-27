## Summary
<!-- Describe what this PR implements and why -->

Closes #

## Squad & Ownership Check
- **Squad:** <!-- squad:contracts | squad:control-plane | squad:ai-director | squad:workers | squad:web-studio | squad:qa-devops -->
- **Agent Used:** <!-- ChatGPT Codex Cloud | Antigravity | Human -->
- [ ] All modified files are strictly inside this squad's allowed paths in `AGENTS.md`
- [ ] Contract / schema changes (if any) were merged via `squad:contracts` first
- [ ] Remotion versions remain pinned cleanly (`4.0.547`)

## Verification Run
- [ ] `npm run typecheck`
- [ ] `npm run build`
- [ ] `ruff check apps/api workers`
- [ ] `cd apps/api && STORAGE_PROVIDER=local RUN_EMBEDDED_ORCHESTRATOR=false pytest -v`

## Notes / Rollback Plan
<!-- Mention any new .env variables, Alembic migrations, or rollback steps -->
