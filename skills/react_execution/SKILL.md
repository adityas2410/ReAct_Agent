---
name: react_execution
description: Run a strict ReAct loop with one action or one final answer per step.
triggers: ["react", "reason", "action", "observation", "tool", "execute", "workflow"]
tools: []
---

# ReAct Execution

When executing a task:

1. Use the assigned instruction as the only task objective.
2. Decide whether a tool call is required before answering.
3. If a tool is required, emit exactly one `Action` with valid JSON arguments.
4. After an observation, decide whether another tool call is needed.
5. Return `Final` when the assigned task is complete or unsupported.
6. Do not invent tools, accounts, files, events, API results, or workflow outputs.
