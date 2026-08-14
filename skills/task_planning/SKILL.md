---
name: task_planning
description: Break one user request into practical subagent tasks.
triggers: ["plan", "planner", "task", "split", "orchestrate", "delegate", "subagent"]
tools: []
---

# Task Planning

When planning a user request:

1. Split the request by executable outcome, not by vague topic.
2. Use one task per independent workflow, such as email, calendar, social, files, or shell.
3. Prefer fewer tasks when one workflow can naturally handle the work.
4. Assign an `agent_type` that matches the capability area.
5. Assign complexity by reasoning load:
   - `low` for direct retrieval or simple formatting
   - `medium` for multi-step workflow execution
   - `high` for ambiguous planning, synthesis, or recovery
6. Do not create a task for an action that has no plausible Skill or MCP tool.
7. Return only the required JSON schema.
