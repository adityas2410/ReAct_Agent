import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class TaskPlanItem:
    agent_type: str
    instruction: str
    complexity: str


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: list[str]
    tools: list[str]
    body: str
    path: str


@dataclass(frozen=True)
class CapabilityRoute:
    mode: str
    skills: list[Skill]
    tools: list[ToolSpec]
    unsupported_reason: str | None = None


class MCPToolClient:
    """
    Connects to an MCP server (mcp_server.py) and exposes:
      - list_tools()
      - call_tool(tool_name, arguments)

    Uses MCP stdio transport by spawning the server as a subprocess.
    """

    def __init__(
        self,
        server_command: str,
        server_args: list[str],
        server_env: Optional[dict[str, str]] = None,
    ) -> None:
        self._server_params = StdioServerParameters(
            command=server_command,
            args=server_args,
            env=server_env,
        )
        self._read = None
        self._write = None
        self._stdio_cm = None
        self._session_cm = None
        self.session: Optional[ClientSession] = None

    async def __aenter__(self) -> "MCPToolClient":
        self._stdio_cm = stdio_client(self._server_params)
        self._read, self._write = await self._stdio_cm.__aenter__()

        self._session_cm = ClientSession(self._read, self._write)
        self.session = await self._session_cm.__aenter__()

        await self.session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(exc_type, exc, tb)

        self.session = None
        self._read = None
        self._write = None
        self._stdio_cm = None
        self._session_cm = None

    async def list_tools(self) -> list[ToolSpec]:
        if self.session is None:
            raise RuntimeError("MCP session not initialized")

        tool_result = await self.session.list_tools()
        tool_items = getattr(tool_result, "tools", tool_result)

        specs: list[ToolSpec] = []
        for t in tool_items:
            name = getattr(t, "name", "")
            description = getattr(t, "description", "") or ""
            input_schema = getattr(t, "inputSchema", None) or {}
            specs.append(ToolSpec(name=name, description=description, input_schema=input_schema))

        return specs

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if self.session is None:
            raise RuntimeError("MCP session not initialized")

        result = await self.session.call_tool(tool_name, arguments=arguments)
        return self._normalize_tool_result(result)

    def _normalize_tool_result(self, result: Any) -> Any:
        if result is None:
            return None

        content = getattr(result, "content", None)
        if content is None:
            return result

        normalized_items: list[Any] = []
        for item in content:
            item_type = getattr(item, "type", None)
            if item_type == "text":
                text = getattr(item, "text", "")
                normalized_items.append(text)
            elif item_type == "json":
                data = getattr(item, "data", None)
                normalized_items.append(data)
            else:
                normalized_items.append({"type": item_type})

        if len(normalized_items) == 1:
            return normalized_items[0]
        return normalized_items


class ModelRouter:
    """
    Local-only model router backed by Ollama.

    Routing policy:
      - low: ollama_small_model
      - medium: ollama_medium_model
      - high: ollama_large_model
    """

    def __init__(
        self,
        ollama_base_url: str = "http://localhost:11434",
        ollama_small_model: str = "llama3.2:3b",
        ollama_medium_model: str = "llama3.1:8b",
        ollama_large_model: str = "qwen2.5:14b",
    ) -> None:
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.ollama_small_model = ollama_small_model
        self.ollama_medium_model = ollama_medium_model
        self.ollama_large_model = ollama_large_model

    def generate(self, messages: list[dict[str, str]], complexity: str) -> str:
        normalized = complexity.lower().strip()
        if normalized == "high":
            return self._generate_ollama(messages, self.ollama_large_model)

        if normalized == "medium":
            return self._generate_ollama(messages, self.ollama_medium_model)

        return self._generate_ollama(messages, self.ollama_small_model)

    def _generate_ollama(self, messages: list[dict[str, str]], model_name: str) -> str:
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
        }
        response = requests.post(f"{self.ollama_base_url}/api/chat", json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        return message.get("content", "")


class SkillStore:
    """
    Loads markdown-only Skills from skills/*/SKILL.md.
    """

    def __init__(self, skills_dir: str = "skills") -> None:
        self.skills_dir = Path(skills_dir)

    def load(self) -> list[Skill]:
        if not self.skills_dir.exists():
            return []

        skills: list[Skill] = []
        for path in sorted(self.skills_dir.glob("*/SKILL.md")):
            skills.append(self._load_skill(path))
        return skills

    def get(self, name: str) -> Skill | None:
        normalized_name = name.lower().strip()
        for skill in self.load():
            if skill.name.lower() == normalized_name:
                return skill
        return None

    def select(self, prompt: str, task: TaskPlanItem, limit: int = 3) -> list[Skill]:
        context = self._normalize_text(f"{prompt} {task.agent_type} {task.instruction}")
        scored: list[tuple[int, Skill]] = []

        for skill in self.load():
            score = 0
            if self._normalize_text(skill.name) in context:
                score += 5

            for piece in skill.name.replace("_", " ").replace("-", " ").split():
                if piece and piece.lower() in context:
                    score += 2

            for trigger in skill.triggers:
                trigger_text = self._normalize_text(trigger)
                if trigger_text and trigger_text in context:
                    score += 3

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [skill for _, skill in scored[:limit]]

    def planning_skills(self) -> list[Skill]:
        skill = self.get("task_planning")
        return [skill] if skill is not None else []

    def _load_skill(self, path: Path) -> Skill:
        raw = path.read_text(encoding="utf-8")
        metadata, body = self._split_front_matter(raw)
        name = str(metadata.get("name") or path.parent.name).strip()
        description = str(metadata.get("description") or "").strip()
        triggers = self._as_list(metadata.get("triggers"))
        tools = self._as_list(metadata.get("tools"))

        return Skill(
            name=name,
            description=description,
            triggers=triggers,
            tools=tools,
            body=body.strip(),
            path=str(path),
        )

    def _split_front_matter(self, raw: str) -> tuple[dict[str, Any], str]:
        if not raw.startswith("---"):
            return {}, raw

        parts = raw.split("---", 2)
        if len(parts) < 3:
            return {}, raw

        metadata: dict[str, Any] = {}
        for line in parts[1].splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            metadata[key] = self._parse_metadata_value(value)

        return metadata, parts[2]

    def _parse_metadata_value(self, value: str) -> Any:
        if value.startswith("[") and value.endswith("]"):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return [item.strip().strip('"') for item in value[1:-1].split(",") if item.strip()]
        return value.strip('"')

    def _as_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(",") if item.strip()]

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.lower().replace("_", " ").replace("-", " ").split())


class CapabilityRouter:
    """
    Resolves each planned task to Skills first, then MCP tools as fallback.
    """

    def __init__(self, skill_store: SkillStore, tools: list[ToolSpec]) -> None:
        self.skill_store = skill_store
        self.tools = tools
        self.tool_index = {tool.name: tool for tool in tools}

    def resolve(self, prompt: str, task: TaskPlanItem) -> CapabilityRoute:
        skills = self.skill_store.select(prompt=prompt, task=task)
        if skills:
            declared_tools = self._declared_tool_names(skills)
            selected_tools = [self.tool_index[name] for name in declared_tools if name in self.tool_index]

            if declared_tools and not selected_tools:
                missing = ", ".join(sorted(declared_tools))
                return CapabilityRoute(
                    mode="unsupported",
                    skills=skills,
                    tools=[],
                    unsupported_reason=f"Matched skill requires unavailable MCP tool(s): {missing}.",
                )

            return CapabilityRoute(mode="skill", skills=skills, tools=selected_tools)

        fallback_tools = self._match_tools(task)
        if fallback_tools:
            return CapabilityRoute(mode="mcp", skills=[], tools=fallback_tools)

        return CapabilityRoute(
            mode="unsupported",
            skills=[],
            tools=[],
            unsupported_reason="No matching Skill or MCP tool is available for this task.",
        )

    def _declared_tool_names(self, skills: list[Skill]) -> list[str]:
        names: list[str] = []
        for skill in skills:
            for name in skill.tools:
                if name not in names:
                    names.append(name)
        return names

    def _match_tools(self, task: TaskPlanItem) -> list[ToolSpec]:
        context = self._tokens(f"{task.agent_type} {task.instruction}")
        matches: list[tuple[int, ToolSpec]] = []

        for tool in self.tools:
            tool_text = self._tokens(f"{tool.name} {tool.description}")
            score = len(context.intersection(tool_text))
            if task.agent_type.lower() in tool.name.lower():
                score += 3
            if score > 0:
                matches.append((score, tool))

        matches.sort(key=lambda item: (-item[0], item[1].name))
        return [tool for _, tool in matches[:3]]

    def _tokens(self, text: str) -> set[str]:
        cleaned = text.lower().replace("_", " ").replace("-", " ")
        raw_tokens = [token.strip(".,:;!?()[]{}\"'") for token in cleaned.split()]
        stopwords = {"the", "and", "for", "with", "from", "that", "this", "into", "task", "user"}
        return {token for token in raw_tokens if len(token) >= 3 and token not in stopwords}


class TaskPlanner:
    """
    Creates a structured task plan from the user prompt.
    """

    def __init__(self, model_router: ModelRouter, skill_store: SkillStore) -> None:
        self.model_router = model_router
        self.skill_store = skill_store

    def plan(self, prompt: str) -> list[TaskPlanItem]:
        planner_prompt = (
            "You are a task planner for a local-first automation orchestrator. "
            "Break the request into executable subagent tasks.\n"
            "Use the planning Skill below when present.\n\n"
            f"Relevant planning Skill:\n{self._format_skills(self.skill_store.planning_skills())}\n\n"
            "Return only JSON with this exact schema:\n"
            '{"tasks":[{"agent_type":"...","instruction":"...","complexity":"low|medium|high"}]}\n'
            "Use as many tasks as needed, but keep them practical and tool-oriented."
        )
        messages = [
            {"role": "system", "content": planner_prompt},
            {"role": "user", "content": prompt},
        ]
        raw = self.model_router.generate(messages, complexity="high")
        plan_json = self._extract_json(raw)

        tasks_raw = plan_json.get("tasks", [])
        if not isinstance(tasks_raw, list) or not tasks_raw:
            raise ValueError("Planner returned an invalid or empty tasks list")

        tasks: list[TaskPlanItem] = []
        for item in tasks_raw:
            if not isinstance(item, dict):
                continue
            agent_type = str(item.get("agent_type", "general")).strip() or "general"
            instruction = str(item.get("instruction", "")).strip()
            complexity = str(item.get("complexity", "low")).strip().lower()

            if not instruction:
                continue
            if complexity not in {"low", "medium", "high"}:
                complexity = "medium"

            tasks.append(
                TaskPlanItem(
                    agent_type=agent_type,
                    instruction=instruction,
                    complexity=complexity,
                )
            )

        if not tasks:
            raise ValueError("Planner produced no usable tasks")

        return tasks

    def _format_skills(self, skills: list[Skill]) -> str:
        if not skills:
            return "- No planning skill available."
        return "\n\n".join(f"# {skill.name}\n{skill.body}" for skill in skills)

    def _extract_json(self, raw: str) -> dict[str, Any]:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass

        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("Planner output did not contain JSON")

        candidate = raw[start : end + 1]
        loaded = json.loads(candidate)
        if not isinstance(loaded, dict):
            raise ValueError("Planner JSON root must be an object")
        return loaded


class SubAgent:
    """
    Worker responsible for one task.

    Skills guide procedure. MCP tools perform actions.
    """

    def __init__(
        self,
        task: TaskPlanItem,
        model_router: ModelRouter,
        route: CapabilityRoute,
        mcp_client: MCPToolClient,
        max_steps: int = 6,
    ) -> None:
        self.task = task
        self.model_router = model_router
        self.route = route
        self.skills = route.skills
        self.tools = route.tools
        self.tool_index = {t.name: t for t in self.tools}
        self.mcp_client = mcp_client
        self.max_steps = max_steps

    async def execute(self) -> dict[str, Any]:
        if self.route.mode == "unsupported":
            return self._result(
                status="unsupported",
                result=self.route.unsupported_reason or "Unsupported task.",
            )

        history: list[dict[str, str]] = []
        user_input = self.task.instruction

        for _ in range(self.max_steps):
            try:
                response = self._chat(user_input=user_input, history=history)
                parsed_type, parsed_payload = self._parse_model_output(response)
            except Exception as exc:
                user_input = (
                    f"Your previous response could not be parsed: {type(exc).__name__}: {exc}.\n"
                    "Respond with exactly one valid Action or Final message."
                )
                continue

            if parsed_type == "final":
                return self._result(status="completed", result=parsed_payload)

            action = parsed_payload
            tool_name = action["tool"]
            arguments = action.get("arguments", {})

            history.append({"role": "assistant", "content": response})

            if tool_name not in self.tool_index:
                user_input = (
                    f"Requested tool '{tool_name}' is unavailable for this task. "
                    f"Available tools: {', '.join(sorted(self.tool_index.keys())) or 'none'}.\n"
                    "Respond with one available Action or a Final message."
                )
                continue

            try:
                tool_output = await self.mcp_client.call_tool(tool_name, arguments)
                observation = json.dumps(tool_output, ensure_ascii=False)
                user_input = (
                    f"Observation from MCP tool '{tool_name}': {observation}\n"
                    "Continue with another Action if needed, otherwise return Final."
                )
            except Exception as exc:
                user_input = (
                    f"MCP tool call failed for '{tool_name}': {type(exc).__name__}: {exc}\n"
                    "Use the error recovery Skill if available, then recover with another Action or return Final."
                )

        return self._result(status="failed", result="Subagent reached max steps without Final output.")

    def _result(self, status: str, result: Any) -> dict[str, Any]:
        return {
            "agent_type": self.task.agent_type,
            "instruction": self.task.instruction,
            "complexity": self.task.complexity,
            "selected_skills": [skill.name for skill in self.skills],
            "available_tools": [tool.name for tool in self.tools],
            "status": status,
            "result": result,
        }

    def _chat(self, user_input: str, history: list[dict[str, str]]) -> str:
        messages = [{"role": "system", "content": self._build_instructions()}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})
        return self.model_router.generate(messages=messages, complexity=self.task.complexity)

    def _build_instructions(self) -> str:
        return (
            f"You are a focused '{self.task.agent_type}' subagent.\n"
            "Skills guide your procedure. MCP tools execute real actions.\n\n"
            "Relevant Skills:\n"
            f"{self._format_skills()}\n\n"
            "Available MCP Tools:\n"
            f"{self._format_tools()}\n\n"
            "Respond in one of these exact formats:\n"
            'Action: {"tool":"<tool_name>","arguments":{...},"reason":"<short reason>"}\n'
            'Final: "<task result>"\n'
            "Use exactly one Action at a time, with valid JSON arguments. "
            "If no available tool can execute the task, return Final explaining the unsupported capability."
        )

    def _format_skills(self) -> str:
        if not self.skills:
            return "- No matching Skill. Use MCP tools directly if one matches the task."

        blocks: list[str] = []
        for skill in self.skills:
            blocks.append(
                f"# {skill.name}\n"
                f"Description: {skill.description or 'No description'}\n"
                f"Allowed MCP tools from this Skill: {', '.join(skill.tools) or 'none'}\n"
                f"{skill.body}"
            )
        return "\n\n".join(blocks)

    def _format_tools(self) -> str:
        if not self.tools:
            return "- No MCP tools available for this routed task."

        tool_lines: list[str] = []
        for tool in self.tools:
            schema_json = json.dumps(tool.input_schema, ensure_ascii=False)
            desc = tool.description.strip() if tool.description else ""
            tool_lines.append(f"- {tool.name}: {desc}\n  input_schema: {schema_json}")
        return "\n".join(tool_lines)

    def _parse_model_output(self, text: str) -> tuple[str, Any]:
        stripped = text.strip()

        if stripped.startswith("Final:"):
            final = stripped[len("Final:") :].strip()
            if final.startswith('"') and final.endswith('"') and len(final) >= 2:
                final = final[1:-1]
            return "final", final

        if stripped.startswith("Action:"):
            payload = stripped[len("Action:") :].strip()
            action = json.loads(payload)
            if not isinstance(action, dict):
                raise ValueError("Action must be a JSON object")
            if not isinstance(action.get("tool"), str) or not action["tool"]:
                raise ValueError("Action.tool must be a non-empty string")
            if not isinstance(action.get("arguments", {}), dict):
                raise ValueError("Action.arguments must be an object")
            return "action", action

        raise ValueError("Subagent output must start with 'Action:' or 'Final:'")


class AgentOrchestrator:
    """
    End-to-end orchestrator:
      1) Plan tasks
      2) Resolve Skills first, MCP tools second
      3) Spawn subagents dynamically
      4) Execute subagents concurrently
      5) Aggregate results
    """

    def __init__(
        self,
        model_router: ModelRouter,
        mcp_client: MCPToolClient,
        tools: list[ToolSpec],
        skill_store: SkillStore,
        max_subagent_steps: int,
    ) -> None:
        self.skill_store = skill_store
        self.planner = TaskPlanner(model_router=model_router, skill_store=skill_store)
        self.model_router = model_router
        self.mcp_client = mcp_client
        self.tools = tools
        self.capability_router = CapabilityRouter(skill_store=skill_store, tools=tools)
        self.max_subagent_steps = max_subagent_steps

    async def run(self, prompt: str) -> str:
        tasks = self.planner.plan(prompt)
        subagents = [
            SubAgent(
                task=task,
                model_router=self.model_router,
                route=self.capability_router.resolve(prompt=prompt, task=task),
                mcp_client=self.mcp_client,
                max_steps=self.max_subagent_steps,
            )
            for task in tasks
        ]

        results = await asyncio.gather(*(agent.execute() for agent in subagents))
        return self._aggregate(prompt=prompt, tasks=tasks, results=results)

    def _aggregate(self, prompt: str, tasks: list[TaskPlanItem], results: list[dict[str, Any]]) -> str:
        tasks_json = [
            {
                "agent_type": task.agent_type,
                "instruction": task.instruction,
                "complexity": task.complexity,
            }
            for task in tasks
        ]

        synthesis_instructions = (
            "You are the orchestrator summarizer.\n"
            "Given the original user request, planned tasks, and each subagent result, "
            "produce a concise final response for the user. "
            "Call out unsupported or failed tasks plainly."
        )
        messages = [
            {"role": "system", "content": synthesis_instructions},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "prompt": prompt,
                        "plan": tasks_json,
                        "subagent_results": results,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        return self.model_router.generate(messages=messages, complexity="high")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Local-first ReAct orchestrator with Skills and MCP tools.")
    parser.add_argument("--prompt", required=True, help="User prompt to run through the orchestrator.")
    parser.add_argument(
        "--ollama-small-model",
        default=os.environ.get("OLLAMA_SMALL_MODEL", "llama3.2:3b"),
        help="Ollama model used for low-complexity routing.",
    )
    parser.add_argument(
        "--ollama-medium-model",
        default=os.environ.get("OLLAMA_MEDIUM_MODEL", "llama3.1:8b"),
        help="Ollama model used for medium-complexity routing.",
    )
    parser.add_argument(
        "--ollama-large-model",
        default=os.environ.get("OLLAMA_LARGE_MODEL", "qwen2.5:14b"),
        help="Ollama model used for high-complexity routing.",
    )
    parser.add_argument(
        "--ollama-base-url",
        default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        help="Base URL of Ollama API.",
    )
    parser.add_argument(
        "--skills-dir",
        default=os.environ.get("SKILLS_DIR", "skills"),
        help="Directory containing markdown Skills as */SKILL.md.",
    )
    parser.add_argument(
        "--mcp-command",
        default=os.environ.get("MCP_SERVER_COMMAND", sys.executable),
        help="Command used to start MCP server process (e.g. python, docker).",
    )
    parser.add_argument(
        "--mcp-args",
        nargs="*",
        default=None,
        help="Optional explicit MCP command args. If provided, --mcp-server is ignored.",
    )
    parser.add_argument(
        "--mcp-server",
        default=os.environ.get("MCP_SERVER_PATH", "mcp_server.py"),
        help="Path to FastMCP server file when using python stdio mode.",
    )
    parser.add_argument(
        "--max-subagent-steps",
        type=int,
        default=6,
        help="Maximum ReAct iterations per subagent.",
    )
    args = parser.parse_args()

    model_router = ModelRouter(
        ollama_base_url=args.ollama_base_url,
        ollama_small_model=args.ollama_small_model,
        ollama_medium_model=args.ollama_medium_model,
        ollama_large_model=args.ollama_large_model,
    )
    skill_store = SkillStore(skills_dir=args.skills_dir)

    server_command = args.mcp_command
    server_args = args.mcp_args if args.mcp_args is not None else [args.mcp_server]

    async with MCPToolClient(server_command=server_command, server_args=server_args) as mcp:
        tool_specs = await mcp.list_tools()
        orchestrator = AgentOrchestrator(
            model_router=model_router,
            mcp_client=mcp,
            tools=tool_specs,
            skill_store=skill_store,
            max_subagent_steps=args.max_subagent_steps,
        )
        final_answer = await orchestrator.run(prompt=args.prompt)
        print(final_answer)


if __name__ == "__main__":
    asyncio.run(main())
