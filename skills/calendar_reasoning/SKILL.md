---
name: calendar_reasoning
description: Convert scheduling requests into calendar fields before calling the calendar MCP workflow.
triggers: ["calendar", "schedule", "meeting", "reminder", "event", "appointment", "tomorrow", "today"]
tools: ["calendar_schedule"]
---

# Calendar Reasoning

Use this Skill for calendar and scheduling tasks.

1. Extract the event title, date, and time from the user request or task instruction.
2. If a required field is missing, return `Final` explaining what is missing instead of guessing.
3. Use `calendar_schedule` only after title, date, and time are clear.
4. Prefer the user's exact wording for event titles unless a concise title is obvious.
5. After the MCP observation, report whether the event or reminder was scheduled.
