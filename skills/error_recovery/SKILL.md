---
name: error_recovery
description: Recover from failed parsing, unavailable tools, MCP errors, and unsupported capability requests.
triggers: ["error", "failed", "failure", "timeout", "unavailable", "unsupported", "recover", "retry"]
tools: []
---

# Error Recovery

Use this Skill when a model response, MCP call, or capability route fails.

1. Do not repeat the exact same failing action without changing arguments or approach.
2. If a tool is unavailable, use only the listed available tools.
3. If required inputs are missing, return `Final` with the missing fields.
4. If the requested capability does not exist, return `Final` explaining that no matching Skill or MCP tool is available.
5. Keep recovery short and focused on completing or safely stopping the assigned task.
