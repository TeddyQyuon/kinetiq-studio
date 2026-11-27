# ADR-0007: Multi-Brain AI Director, ChatGPT Plus Bridge & Antigravity MCP

- **Status:** Accepted
- **Date:** 2026-11-27

## Context
The creator holds **Google AI Pro**, **ChatGPT Plus**, and uses **Antigravity**. While Google AI Pro provides official Gemini API credits via Google AI Studio, ChatGPT Plus is a consumer subscription with no developer API access (automating the ChatGPT web UI violates OpenAI Terms of Service and risks account bans).

## Decision
Implement a unified `BrainProvider` abstraction and shared Pydantic tool registry supporting four compliant workflows:
1. **`GeminiProvider` (Default Auto):** Uses official Gemini API (`Pro` tier for planning/copilot, `Flash` tier for prompt compilation and HOTL Vision QA).
2. **`ManualBridgeProvider` (ChatGPT Plus Co-Director):** Generates a 1-click **Director Brief** prompt containing the project goals and `TimelineDocument` schema for pasting into ChatGPT Plus, then validates and imports ChatGPT's JSON response cleanly.
3. **`OpenAIProvider` (Optional BYOK):** Standard API integration if an OpenAI developer key is configured.
4. **Antigravity Streamable HTTP MCP (`/mcp`):** Exposes the exact same timeline read/command tools to Antigravity for deep agentic edits.

## Consequences
- **Positive:** 100% ToS-compliant access to all user AI subscriptions, zero vendor lock-in, and consistent schema validation regardless of which AI generated the plan or edit.
- **Negative:** ChatGPT Plus mode involves one manual copy/paste step for initial planning unless using an OpenAI API key.
