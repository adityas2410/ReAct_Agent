---
name: email_reasoning
description: Decide how to process email automation requests before calling the email MCP workflow.
triggers: ["email", "emails", "inbox", "unread", "thread", "threads", "urgent", "follow-up", "followup"]
tools: ["email_process", "daily_summary"]
---

# Email Reasoning

Use this Skill for email and inbox tasks.

1. Identify the requested email mode: summary, urgent, cleanup, or daily summary.
2. Preserve user constraints such as date, unread-only, sender, urgency, or follow-up intent.
3. Use `email_process` for targeted email operations.
4. Use `daily_summary` only when the request asks for a broad day-level automation summary.
5. After the MCP observation, summarize concrete results and mention any missing capability plainly.
