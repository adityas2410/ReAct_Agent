# ReAct AI Agent Orchestrator for Spawning Parallel Subagents with MCP

Personal CLI AI agent using local/remote LLMs and **ReAct (Reasoning + Action)** with an MCP server for tool calling.

This project implements:
- **Model routing** (Ollama for low/medium complexity, OpenAI for high complexity)
- **Task planning** from a single user prompt
- **Dynamic subagent spawning**
- **Concurrent execution** of subagents via `asyncio.gather(...)`
- **MCP tool calls** to run automated workflows (for example n8n-backed email/calendar/social actions)

---

## What this repo is

A Python CLI orchestrator where:
1. You provide one prompt.
2. A planner creates structured sub-tasks.
3. Subagents execute those tasks in parallel using ReAct loops.
4. Subagents call MCP tools.
5. Final output is aggregated into one user-facing response.

`mcp_server.py` exposes tool endpoints, and `react_agent.py` is the orchestrator runtime.

---

## High-Level Architecture

```mermaid
flowchart TD
    U[User Prompt via CLI] --> O[AgentOrchestrator]
    O --> P[TaskPlanner]
    P --> TP["Task Plan JSON (tasks)"]

    TP --> SA1[SubAgent A]
    TP --> SA2[SubAgent B]
    TP --> SA3[SubAgent C]

    SA1 --> MR1[ModelRouter]
    SA2 --> MR2[ModelRouter]
    SA3 --> MR3[ModelRouter]

    MR1 -->|low/medium| OL1[Ollama]
    MR2 -->|low/medium| OL2[Ollama]
    MR3 -->|high| OA1[OpenAI]

    SA1 --> MCP[MCPToolClient]
    SA2 --> MCP
    SA3 --> MCP

    MCP --> MS[mcp_server.py tools]
    MS --> WF[n8n / external workflow actions]
    WF --> SA1
    WF --> SA2
    WF --> SA3

    SA1 --> AGG[Final Aggregation]
    SA2 --> AGG
    SA3 --> AGG
    AGG --> OUT[Final Answer]
```

---

## Core Components

### 1) ModelRouter
Routes model calls by task complexity:
- `low` → Ollama small model (default `gemma3`)
- `medium` → Ollama larger model (default `llama4`)
- `high` → OpenAI model (default `gpt-5.2`)

This allows local models for cheaper/faster simple tasks while escalating harder reasoning to OpenAI.

### 2) TaskPlanner
Generates a structured plan before execution:

```json
{
  "tasks": [
    {
      "agent_type": "email",
      "instruction": "summarize inbox and identify urgent threads",
      "complexity": "low"
    },
    {
      "agent_type": "calendar",
      "instruction": "schedule follow-up reminders tomorrow at 10:00",
      "complexity": "medium"
    }
  ]
}
```

### 3) SubAgent
Each planned task becomes a `SubAgent` worker with:
- task instruction
- MCP tool access
- complexity-aware model routing

Each subagent uses a strict ReAct loop format:
- `Action: {"tool":"...","arguments":{...},"reason":"..."}`
- `Final: "..."`

### 4) Concurrent execution
All subagents are run in parallel:

```python
results = await asyncio.gather(*(agent.execute() for agent in subagents))
```

### 5) MCP integration
Subagents call tools through `MCPToolClient`, which communicates with `mcp_server.py` over stdio.
Each MCP tool can trigger an automation workflow (for example n8n webhooks).
The MCP server also exposes `bash_execute` so subagents can run local shell commands for file navigation/CLI tasks.

---

## ReAct Loop (inside each SubAgent)

1. Send current context to routed model.
2. Parse model output as either `Action` or `Final`.
3. If `Action`:
   - validate tool name + arguments
   - call MCP tool
   - append observation to next prompt
4. If `Final`: return result for aggregation.
5. Repeat until completion or max steps.

---

## CLI Usage

### Basic
```bash
python react_agent.py --prompt "Summarize unread emails and schedule follow-up tomorrow at 10am"
```

### With explicit model routing config
```bash
python react_agent.py \
  --prompt "Summarize unread emails and schedule follow-up tomorrow at 10am" \
  --openai-model gpt-5.2 \
  --ollama-small-model gemma3 \
  --ollama-large-model llama4 \
  --ollama-base-url http://localhost:11434 \
  --mcp-server mcp_server.py \
  --max-subagent-steps 6
```

---

## End-to-End Example: Prompt → Routing → Parallel Subagents → Workflow

### Input Prompt
```text
Summarize unread emails, draft a social update from key highlights, and schedule follow-up reminders tomorrow at 10am.
```

### Planned Tasks 
```json
{
  "tasks": [
    {"agent_type": "email", "instruction": "summarize unread inbox and urgent items", "complexity": "low"},
    {"agent_type": "social", "instruction": "draft a short post from the summary", "complexity": "medium"},
    {"agent_type": "calendar", "instruction": "schedule follow-up reminders tomorrow at 10:00", "complexity": "high"}
  ]
}
```

### Model Routing Decisions (illustrative)
- Email subagent (`low`) → **Ollama / gemma3**
- Social subagent (`medium`) → **Ollama / llama4**
- Calendar subagent (`high`) → **OpenAI / gpt-5.2**

### Parallel Subagent Work (illustrative)
- SubAgent(email) calls MCP tool: `email_process`
- SubAgent(social) calls MCP tool: `social_post`
- SubAgent(calendar) calls MCP tool: `calendar_schedule`
- SubAgent(ops) can call MCP tool: `bash_execute`

All run concurrently and return individual results.

### Aggregated Final Output (illustrative)
```text
Done. I summarized your unread emails and highlighted urgent threads, drafted and posted a social update with the key highlights, and scheduled follow-up reminders for tomorrow at 10:00.
```

---

## Notes

- The orchestrator currently runs one prompt per CLI invocation.
- MCP server logic lives in `mcp_server.py`; orchestrator logic lives in `react_agent.py`.
- You can swap models via CLI flags/environment variables without changing orchestration code.
