---
name: skill_evolution
description: Propose safe reusable Skill improvements from completed run traces.
triggers: ["skill", "evolve", "evolution", "proposal", "memory", "trace", "learning", "experience"]
tools: []
---

# Skill Evolution

Use this Skill after a run trace has been saved.

1. Propose a Skill update only when the run reveals reusable behavior, repeated failure, missing capability, or a clearer procedure.
2. Prefer small additions to existing Skills over broad rewrites.
3. Never overwrite or directly edit `SKILL.md` files during execution.
4. Return proposal JSON only.
5. Use `missing_capability` when the user requested a capability with no matching Skill or MCP tool.
6. Use `skill_update` when an existing Skill should learn a better instruction.
7. Keep the proposed change operational, specific, and short.
