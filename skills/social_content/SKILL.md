---
name: social_content
description: Draft and route social posting requests to the social MCP workflow.
triggers: ["social", "post", "tweet", "linkedin", "x", "caption", "announcement", "update"]
tools: ["social_post"]
---

# Social Content

Use this Skill for social drafting or posting tasks.

1. Identify the platform requested by the user.
2. Draft concise content that matches the platform and source material.
3. Do not claim a post was published unless the MCP observation confirms it.
4. Use `social_post` only when the request asks to post or publish.
5. If the user only asks for a draft, return `Final` with the draft and do not call a tool.
