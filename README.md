# ReAct AI Agent Orchestrator

Personal AI agent orchestrator built around local LLMs, ReAct loops, dynamic subagents, self-hosted MCP tools, and markdown Skills that improve from run experience.

The project is designed for local-first automation: Ollama handles model inference, FastMCP exposes executable tools and workflows, Skills provide reusable task procedures, Docker isolates risky execution, and Langfuse records the full agent trace.

## Stack

- Python
- Ollama
- FastMCP
- Markdown Skills
- Langfuse
- Docker
- n8n-compatible workflow hooks

## What It Does

A single user prompt is planned into smaller tasks, then executed by focused subagents running concurrently.

Each subagent follows this routing order:

```text
1. Check for a matching Skill
2. Use the Skill as the task procedure
3. Call MCP tools for execution when needed
4. If no Skill or MCP tool exists, mark the task unsupported
```

This keeps the agent grounded: Skills define how the agent should work, while MCP tools perform real actions such as email processing, calendar scheduling, file generation, shell execution, or n8n workflow calls.

## Architecture

```mermaid
flowchart TD
    U[User Prompt] --> O[AgentOrchestrator]
    O --> P[TaskPlanner]
    P --> T[Structured Task Plan]

    T --> SA1[Email Subagent]
    T --> SA2[Calendar Subagent]
    T --> SA3[Social Subagent]

    SA1 --> CR1[Capability Router]
    SA2 --> CR2[Capability Router]
    SA3 --> CR3[Capability Router]

    CR1 --> SK1[Matching Skill]
    CR2 --> SK2[Matching Skill]
    CR3 --> SK3[Matching Skill]

    SK1 --> MR1[Local Model Router]
    SK2 --> MR2[Local Model Router]
    SK3 --> MR3[Local Model Router]

    MR1 --> OL1[Ollama low model]
    MR2 --> OL2[Ollama medium model]
    MR3 --> OL3[Ollama high model]

    SA1 --> MCP[MCP Tool Client]
    SA2 --> MCP
    SA3 --> MCP

    MCP --> GOV[Governance Policy]
    GOV --> MS[FastMCP Server]
    MS --> N8N[n8n workflows / APIs]
    MS --> FS[File tools]
    MS --> SH[Sandboxed shell]

    SA1 --> AGG[Final Aggregation]
    SA2 --> AGG
    SA3 --> AGG
    AGG --> OUT[Final Answer]

    O --> MEM[Run Memory]
    MEM --> PROP[Skill Proposal]

    O --> LF[Langfuse Trace]
    SA1 --> LF
    SA2 --> LF
    SA3 --> LF
    MCP --> LF
```

## Core Concepts

### ReAct Agent Loop

Each subagent runs a Reasoning + Action loop:

```text
Thought is handled by the model context.
Action: {"tool":"tool_name","arguments":{},"reason":"why this tool is needed"}
Observation: tool result
Final: "task result"
```

The loop repeats until the subagent returns `Final` or reaches the configured step limit. Each step is saved into the run trace with model output, parsed action, observation, errors, and recovery prompts.

### Local Model Routing

The router maps task complexity to local Ollama models:

```text
low    -> small local model
medium -> balanced local model
high   -> strongest local model
```

This preserves a local-first design while still allowing different models to be used for different task difficulty levels.

### Skills

Skills are markdown procedures stored under `skills/`.

Current Skills:

```text
skills/
  task_planning/SKILL.md
  react_execution/SKILL.md
  email_reasoning/SKILL.md
  calendar_reasoning/SKILL.md
  social_content/SKILL.md
  error_recovery/SKILL.md
  skill_evolution/SKILL.md
```

Skills do not duplicate MCP workflows. They teach the agent how to approach a task, choose fields, recover from errors, and decide when a tool call is needed.

### MCP Tools

MCP is the execution layer. The FastMCP server exposes tools such as:

```text
email_process
calendar_schedule
social_post
daily_summary
bash_execute
write_txt_file
write_markdown_file
write_csv_file
write_json_file
write_docx_file
```

These tools can trigger n8n workflows, call external APIs, write files, or run local commands inside a restricted workspace.

### Governance Policy

Governance is local policy enforcement before MCP execution. It decides whether a proposed tool call is allowed, denied, or requires user approval.

```text
Model proposes MCP tool call
PolicyEngine checks tool + arguments
allow / approval_required / deny
Only allowed calls reach MCP
```

The default policy is stored in `governance_policy.json`:

```text
email_process summary/urgent -> allow
calendar_schedule -> approval required
social_post -> approval required
bash_execute -> approval required unless denied
file writes -> allowed only inside configured workspace/output dirs
dangerous shell patterns -> deny
unknown tools -> deny
```

Every policy decision is saved into local run memory and emitted to Langfuse when tracing is enabled.

### Run Memory

Each CLI run saves one structured JSON trace under `memory/runs/`.

The trace includes:

```text
prompt
model config
available MCP tools
planned tasks
selected skills
available tools per subagent
policy decisions and approval status
subagent statuses
ReAct step history
unsupported tasks
final answer
skill proposal path, if one was generated
```

### Self-Evolving Skills

The agent does not blindly rewrite its own Skills. Instead, each run can produce a skill update proposal and ask for approval immediately.

```text
1. Save run trace to memory/runs/
2. Reflect on successful and failed steps
3. Generate a JSON skill proposal
4. Show the proposal in the CLI
5. Ask whether to apply it
6. If approved, update or create the Skill
7. If rejected, delete the proposal
```

Unsupported tasks automatically create `missing_capability` proposals. Normal runs may create `skill_update` proposals when the local model finds reusable learning in the trace. Approved `skill_update` proposals append to `## Learned Updates` in the target Skill. Approved `missing_capability` proposals create a new `skills/<capability>/SKILL.md` stub. Rejected proposals are removed instead of accumulating.

### Langfuse Observability

Langfuse is optional and observes the custom Python loop directly. It does not require LangChain, CrewAI, OpenAI Agents SDK, or any other agent framework.

When `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are configured, the agent traces the full orchestration lifecycle:

```text
root prompt
planner call
skill selection
subagent execution
ReAct steps
model calls
MCP tool calls
final aggregation
skill evolution proposal
```

If Langfuse is not configured, the agent still runs normally and saves local traces under `memory/runs/`.

### Docker Sandbox

Risky tools such as shell execution and file writes should run through the MCP server inside Docker. The MCP server also enforces `MCP_WORKSPACE_DIR`, so file writes and shell working directories cannot escape the mounted workspace.

The MCP container can be restricted with:

```text
read-only filesystem
limited mounted workspace
no-new-privileges
dropped Linux capabilities
non-root user
optional network isolation for local-only tools
```

For workflows that require n8n or external APIs, the automation MCP server can run with network access while local shell/file tools remain sandboxed separately.

## Example Flow

Prompt:

```text
Summarize unread emails and schedule follow-up reminders tomorrow at 10am.
```

Planner output:

```json
{
  "tasks": [
    {
      "agent_type": "email",
      "instruction": "Summarize unread emails and identify urgent follow-ups.",
      "complexity": "low"
    },
    {
      "agent_type": "calendar",
      "instruction": "Schedule follow-up reminders tomorrow at 10am.",
      "complexity": "medium"
    }
  ]
}
```

Execution:

```text
EmailSubAgent
  -> loads email_reasoning Skill
  -> calls MCP email_process
  -> returns email summary

CalendarSubAgent
  -> loads calendar_reasoning Skill
  -> calls MCP calendar_schedule
  -> returns scheduling result
```

Both subagents execute concurrently, then the orchestrator aggregates the final response, saves run memory, and may ask for approval to apply a Skill proposal.

## CLI Usage

Basic run:

```bash
python react_agent.py --prompt "Summarize unread emails and schedule follow-up tomorrow at 10am"
```

With local model routing, Skills, and memory:

```bash
python react_agent.py \
  --prompt "Summarize unread emails and schedule follow-up tomorrow at 10am" \
  --ollama-small-model llama3.2:3b \
  --ollama-medium-model llama3.1:8b \
  --ollama-large-model qwen2.5:14b \
  --ollama-base-url http://localhost:11434 \
  --skills-dir skills \
  --memory-dir memory \
  --governance-policy governance_policy.json \
  --mcp-server mcp_server.py \
  --max-subagent-steps 6
```

With optional self-hosted Langfuse:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000

python react_agent.py \
  --prompt "Summarize unread emails" \
  --skills-dir skills \
  --memory-dir memory
```

Disable proposal generation while still saving run memory:

```bash
python react_agent.py \
  --prompt "Summarize unread emails" \
  --disable-skill-evolution
```

Disable Langfuse while keeping local run memory:

```bash
python react_agent.py \
  --prompt "Summarize unread emails" \
  --disable-langfuse
```

Disable governance for legacy behavior:

```bash
python react_agent.py \
  --prompt "Summarize unread emails" \
  --disable-governance
```

Auto-approve only tools listed in `safe_auto_approve_tools`:

```bash
python react_agent.py \
  --prompt "Write a local report" \
  --auto-approve-safe-tools
```

Dockerized MCP server:

```bash
docker build -f Dockerfile.mcp -t react-mcp-server:latest .

python react_agent.py \
  --prompt "Generate a markdown report from my email summary" \
  --skills-dir skills \
  --memory-dir memory \
  --mcp-command docker \
  --mcp-args run --rm -i \
    --read-only \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --tmpfs /tmp \
    -e MCP_WORKSPACE_DIR=/workspace \
    -e N8N_WEBHOOK_BASE="$N8N_WEBHOOK_BASE" \
    -v "$PWD/workspace:/workspace:rw" \
    react-mcp-server:latest
```

## Environment

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_SMALL_MODEL=llama3.2:3b
OLLAMA_MEDIUM_MODEL=llama3.1:8b
OLLAMA_LARGE_MODEL=qwen2.5:14b
SKILLS_DIR=skills
MEMORY_DIR=memory
GOVERNANCE_POLICY=governance_policy.json
MCP_SERVER_PATH=mcp_server.py
MCP_WORKSPACE_DIR=workspace
N8N_WEBHOOK_BASE=https://your-n8n-domain/webhook
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_BASE_URL=
```

## Repository Direction

The intended implementation path is:

```text
1. Local-only Ollama model routing
2. Markdown SkillStore and skill selection
3. Capability router with Skill-first, MCP-fallback behavior
4. Per-run memory traces
5. Skill proposal generation from run experience
6. Langfuse tracing across the agent lifecycle
7. Governance checks before MCP execution
8. Docker-restricted MCP execution
```

## Design Rule

```text
Skills decide how to work.
MCP performs actions.
Governance controls whether actions are allowed.
Langfuse observes the run.
Docker contains risky execution.
```
