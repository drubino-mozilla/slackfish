# Slackfish

A Firefox extension that passively captures Slack data as you browse and exposes it to AI agents via MCP.

## How It Works

Slackfish intercepts Slack's own API responses as they flow through Firefox — no external tokens, no admin approval, no detectable footprint. When you browse channels, read DMs, or open threads, the extension captures the structured JSON that Slack's servers return and caches it locally.

A Python MCP server then serves this cached data to your AI assistant (Cursor, Claude Code, etc.).

## *Slackfish is under development and not ready for testing yet*