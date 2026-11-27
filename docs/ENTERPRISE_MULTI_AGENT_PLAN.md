# Kinetiq Studio — 30-Day Enterprise Multi-Agent Engineering Plan

You are operating as **Chief Architect & Release Manager** leading **6 specialized AI developer squads** powered by **ChatGPT Business (Codex Cloud + `@codex review`)** and **Google Antigravity (`/teamwork`, `/boost`, `/goal`, Browser Subagent, Git Worktrees)**.

This playbook lets one founder ship production enterprise features in parallel at team velocity—with **zero merge conflicts** and **automated CI + AI code review gates**.

---

## 1. Operating Model: How Your Two AI Teams Work Together

| Tooling | Best Assigned Work | Why It Excels Here |
| :--- | :--- | :--- |
| **ChatGPT Business (Codex Cloud)** | Backend services, Python workers, FFmpeg/Kokoro/WhisperX pipelines, Redis/Drive reconcilers, security middleware (`squad:control-plane`, `squad:workers`, `squad:ai-director`) | Runs multiple isolated cloud sandboxes in parallel straight from your GitHub repo (`TeddyQyuon/kinetiq-studio`), runs `pytest`/`ruff`, and opens clean PRs while you work on other things. |
| **ChatGPT Business (`@codex review`)** | Automated PR review on **every** pull request | Reads `AGENTS.md` and audits every diff for race conditions, security flaws, chunk-alignment bugs, and path-boundary violations before you merge. |
| **Google Antigravity (`/teamwork` + `/boost`)** | Multi-file UI features, Remotion Studio timelines, OPFS/IndexedDB browser state, Playwright E2E, visual verification via Browser Subagent (`squad:web-studio`, `squad:qa-devops`, `squad:contracts`) | Interactive local IDE/agent orchestration with multi-step reasoning, visual browser testing on `localhost:3000`, and `/mcp` integration. |
| **Git Worktrees (`scripts/new-worktree.ps1`)** | Running 2–3 Antigravity sessions locally at the same time | Gives each Antigravity task its own isolated folder (`..\kinetiq-<slug>`) and branch so local agents never overwrite each other's files. |

---

## 2. The Zero-Conflict Squad Map

Every GitHub Issue is locked to **one Squad** and **one exclusive directory zone** (enforced in `AGENTS.md`):

```text
TeddyQyuon/kinetiq-studio (main — protected by CI)
 ├── squad:contracts      ──► packages/contracts, models.py, alembic/   (Land FIRST)
 ├── squad:control-plane  ──► core/, identity/, storage/, jobs/, events/ (Codex Cloud)
 ├── squad:ai-director    ──► director/, qa/, mcp/, projects/, timeline/ (Codex / AGY)
 ├── squad:workers        ──► workers/*, app/workers/, exports/         (Codex Cloud)
 ├── squad:web-studio     ──► apps/web/, packages/remotion-studio/      (Antigravity)
 └── squad:qa-devops      ──► .github/, infra/, apps/api/tests/, docs/   (AGY / CI)
```

**Golden Rule:**
- **Never** run two parallel agents inside the same squad zone at the same time unless they touch completely separate files.
- If a feature needs a new API field or timeline command, land a tiny **`squad:contracts` PR first**, merge to `main`, and only then dispatch the backend + frontend squads in parallel.

---

## 3. 30-Day Execution Roadmap (4 Weekly Waves)

### Wave 1 (Days 1–7) — Core Hardening & Media Workers
Run **3 squads in parallel** right away (zero file overlap):
1. **Issue #1 (`squad:control-plane` · Codex Cloud):** Redis Pub/Sub SSE Fan-Out & Google Drive Vault Reconciler.
2. **Issue #2 (`squad:workers` · Codex Cloud):** `workers/media` Runner — Kokoro-82M Per-Scene TTS, WhisperX Word Alignment & 540p Proxy Transcoder.
3. **Issue #3 (`squad:web-studio` · Antigravity Worktree):** Dexie IndexedDB Crash-Safe Offline Autosave & Live `EventSource` (`Last-Event-ID`) Progress Sync.

### Wave 2 (Days 8–14) — Master Renderer & Interactive Timeline
1. **Issue #4 (`squad:workers` · Codex Cloud):** `workers/render` Runner — Headless Node Remotion Render + FFmpeg `-14 LUFS` Loudnorm, `-26 dB` Music Ducking & SRT Export.
2. **Issue #5 (`squad:ai-director` · Codex Cloud):** Multimodal 4-Frame Video Clip QA, `OpenAIProvider` BYOK & Zero-Key `colab_ai` Worker Mode.
3. **Issue #6 (`squad:web-studio` · Antigravity Worktree + Browser Subagent):** Interactive Multi-Track Timeline (Drag-Reorder, Edge-Trim, Playhead Split) + Browser OPFS 540p Proxy Cache.

### Wave 3 (Days 15–21) — Advanced AI Copilot, 4K & Security
1. **Issue #7 (`squad:ai-director` · Antigravity):** Full-Draft Multimodal Review (`review_draft` Copilot Tool) & QA Prompt Pass-Rate Analytics.
2. **Issue #8 (`squad:control-plane` · Codex Cloud):** HttpOnly Session Cookie Auth, CSRF Protection & Per-Worker Token Rate Limiting.
3. **Issue #9 (`squad:workers` · Codex Cloud):** `SeedVR2-3B FP8` High-Detail 4K Workflow & Automatic Colab A100 → L4 Fallback.

### Wave 4 (Days 22–30) — Playwright E2E, Telemetry & VPS Launch
1. **Issue #10 (`squad:qa-devops` · Antigravity `/goal`):** Playwright Full-Pipeline E2E Suite (`--mock` Worker), System Health Dashboard & Phase 1 VPS Deploy Script (`infra/docker-compose.vps.yml` + Caddy).

---

## 4. Daily Chief-Architect Workflow (Step-by-Step)

### A. Dispatching Parallel Tasks in ChatGPT Business (Codex)
1. Open **ChatGPT Business → Codex**, connect `TeddyQyuon/kinetiq-studio`.
2. Open 2–3 parallel Codex tasks simultaneously for different squads (for example: Issue #1 + Issue #2).
3. Use this **Codex Task Prompt Template**:
   ```text
   Read AGENTS.md and docs/ARCHITECTURE.md first.
   Implement GitHub Issue #<NUMBER> (<TITLE>) on branch feat/sq<NUMBER>-<slug>.
   Strictly modify ONLY files in your squad's allowed paths: <ALLOWED_PATHS>.
   Run `ruff check apps/api workers` and `cd apps/api && STORAGE_PROVIDER=local RUN_EMBEDDED_ORCHESTRATOR=false pytest -v` before finishing.
   Open a PR with `Closes #<NUMBER>` in the description.
   ```

### B. Dispatching Parallel Local Tasks in Antigravity (Git Worktrees)
Never run two Antigravity tasks in the same folder! Create an isolated worktree per branch:
```powershell
# From C:\Users\Administrator\Downloads\kinetiq-studio
.\scripts\new-worktree.ps1 -Branch feat/sq3-offline-autosave -InstallDeps
```
Then open `C:\Users\Administrator\Downloads\kinetiq-feat-sq3-offline-autosave` in a new Antigravity window and use:
- **`/boost`** for complex architecture/state-machine work (like Issue #6 OPFS + timeline dragging).
- **`/teamwork`** when splitting frontend UI + Remotion templates + Playwright tests across subagents.
- **`/goal`** for unattended verification runs (e.g., making all E2E tests pass).
- **`/browser`** to visually inspect `http://localhost:3000/projects/prj_demo_reel_01/editor` and verify zero console errors.

### C. Review, Merge & Cleanup Loop (Every Evening)
1. Wait for GitHub Actions CI (`web-and-contracts` + `api-and-workers`) to turn green.
2. Comment `@codex review` on any PR you want audited against `AGENTS.md`.
3. Inspect the PR diff in GitHub, click **Squash and merge**, and delete the remote branch.
4. Clean up your local worktree:
   ```powershell
   .\scripts\remove-worktree.ps1 -Branch feat/sq3-offline-autosave
   ```
5. Pull `main` (`git pull origin main`) before starting the next Wave!

---

## 5. Activating GitHub Actions CI (1-Time Step)

A complete CI pipeline (`web-and-contracts` on Node 22 + `api-and-workers` on Python 3.12 with `ruff` and `pytest`) is saved at [`infra/ci.workflow.yml`](../infra/ci.workflow.yml). Because GitHub requires the `workflow` OAuth scope to push files under `.github/workflows/`, activate it once using either method:

1. **Via CLI:**
   ```powershell
   gh auth refresh -h github.com -s workflow
   mkdir .github\workflows
   Move-Item infra\ci.workflow.yml .github\workflows\ci.yml
   git add .github/workflows/ci.yml infra/ci.workflow.yml
   git commit -m "ci: enable GitHub Actions CI workflow"
   git push origin main
   ```
2. **Via GitHub Web UI:** Open `TeddyQyuon/kinetiq-studio` → **Actions → set up a workflow yourself** → paste the contents of `infra/ci.workflow.yml` as `.github/workflows/ci.yml` and commit directly on GitHub.

