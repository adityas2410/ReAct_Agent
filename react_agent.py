import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional

import requests
from openai import OpenAI

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

        tools = await self.session.list_tools()

        specs: list[ToolSpec] = []
        for t in tools:
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
    Provider-aware model router.

    Routing policy:
      - low: ollama_small_model
      - medium: ollama_large_model
      - high: openai_model
    """

    def __init__(
        self,
        openai_client: OpenAI,
        openai_model: str,
        ollama_base_url: str = "http://localhost:11434",
        ollama_small_model: str = "gemma3",
        ollama_large_model: str = "llama4",
    ) -> None:
        self.openai_client = openai_client
        self.openai_model = openai_model
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.ollama_small_model = ollama_small_model
        self.ollama_large_model = ollama_large_model

    def generate(self, messages: list[dict[str, str]], complexity: str) -> str:
        normalized = complexity.lower().strip()
        if normalized == "high":
            return self._generate_openai(messages)

        if normalized == "medium":
            return self._generate_ollama(messages, self.ollama_large_model)

        return self._generate_ollama(messages, self.ollama_small_model)

    def _generate_openai(self, messages: list[dict[str, str]]) -> str:
        completion = self.openai_client.chat.completions.create(
            model=self.openai_model,
            messages=messages,
        )
        return completion.choices[0].message.content or ""

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


class TaskPlanner:
    """
    Creates a structured task plan from the user prompt.
    """

    def __init__(self, model_router: ModelRouter) -> None:
        self.model_router = model_router

    def plan(self, prompt: str) -> list[TaskPlanItem]:
        planner_prompt = (
            "You are a task planner for an automation orchestrator. "
            "Break the request into executable subagent tasks.\n"
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

    Each subagent can choose MCP tools in a ReAct loop and uses ModelRouter
    for model-provider selection by task complexity.
    """

    def __init__(
        self,
        task: TaskPlanItem,
        model_router: ModelRouter,
        tools: list[ToolSpec],
        mcp_client: MCPToolClient,
        max_steps: int = 6,
    ) -> None:
        self.task = task
        self.model_router = model_router
        self.tools = tools
        self.tool_index = {t.name: t for t in tools}
        self.mcp_client = mcp_client
        self.max_steps = max_steps

    async def execute(self) -> dict[str, Any]:
        history: list[dict[str, str]] = []
        user_input = self.task.instruction

        for _ in range(self.max_steps):
            response = self._chat(user_input=user_input, history=history)
            parsed_type, parsed_payload = self._parse_model_output(response)

            if parsed_type == "final":
                return {
                    "agent_type": self.task.agent_type,
                    "instruction": self.task.instruction,
                    "complexity": self.task.complexity,
                    "result": parsed_payload,
                }

            action = parsed_payload
            tool_name = action["tool"]
            arguments = action.get("arguments", {})

            history.append({"role": "assistant", "content": response})

            if tool_name not in self.tool_index:
                user_input = (
                    f"Requested tool '{tool_name}' is unavailable. "
                    f"Available tools: {', '.join(sorted(self.tool_index.keys()))}.\n"
                    "Respond with one Action or a Final message using required format."
                )
                continue

            try:
                tool_output = await self.mcp_client.call_tool(tool_name, arguments)
                observation = json.dumps(tool_output, ensure_ascii=False)
                user_input = (
                    f"Observation from tool '{tool_name}': {observation}\n"
                    "Continue with another Action if needed, otherwise return Final."
                )
            except Exception as exc:
                user_input = (
                    f"Tool call failed for '{tool_name}': {type(exc).__name__}: {exc}\n"
                    "Recover with a different Action or return Final."
                )

        return {
            "agent_type": self.task.agent_type,
            "instruction": self.task.instruction,
            "complexity": self.task.complexity,
            "result": "Subagent reached max steps without Final output.",
        }

    def _chat(self, user_input: str, history: list[dict[str, str]]) -> str:
        messages = [{"role": "system", "content": self._build_instructions()}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})
        return self.model_router.generate(messages=messages, complexity=self.task.complexity)

    def _build_instructions(self) -> str:
        tool_lines: list[str] = []
        for t in self.tools:
            schema_json = json.dumps(t.input_schema, ensure_ascii=False)
            desc = t.description.strip() if t.description else ""
            tool_lines.append(f"- {t.name}: {desc}\n  input_schema: {schema_json}")

        tools_block = "\n".join(tool_lines) if tool_lines else "- (no tools available)"

        return (
            f"You are a focused '{self.task.agent_type}' subagent.\n"
            "Solve the assigned task using MCP tools when required.\n"
            "Available tools:\n"
            f"{tools_block}\n\n"
            "Respond in one of these exact formats:\n"
            'Action: {"tool":"<tool_name>","arguments":{...},"reason":"<short reason>"}\n'
            'Final: "<task result>"\n'
            "Use exactly one Action at a time, with valid JSON arguments."
        )

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
      2) Spawn subagents dynamically
      3) Execute subagents concurrently
      4) Aggregate results
    """

    def __init__(
        self,
        model_router: ModelRouter,
        mcp_client: MCPToolClient,
        tools: list[ToolSpec],
        max_subagent_steps: int,
    ) -> None:
        self.planner = TaskPlanner(model_router=model_router)
        self.model_router = model_router
        self.mcp_client = mcp_client
        self.tools = tools
        self.max_subagent_steps = max_subagent_steps

    async def run(self, prompt: str) -> str:
        tasks = self.planner.plan(prompt)
        subagents = [
            SubAgent(
                task=task,
                model_router=self.model_router,
                tools=self.tools,
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
            "produce a concise final response for the user."
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
    parser = argparse.ArgumentParser(description="Agent orchestrator with MCP tool execution and model routing.")
    parser.add_argument("--prompt", required=True, help="User prompt to run through the orchestrator.")
    parser.add_argument(
        "--openai-model",
        default=os.environ.get("OPENAI_MODEL", "gpt-5.2"),
        help="OpenAI model used for high-complexity routing.",
    )
    parser.add_argument(
        "--ollama-small-model",
        default=os.environ.get("OLLAMA_SMALL_MODEL", "gemma3"),
        help="Ollama model used for low-complexity routing.",
    )
    parser.add_argument(
        "--ollama-large-model",
        default=os.environ.get("OLLAMA_LARGE_MODEL", "llama4"),
        help="Ollama model used for medium-complexity routing.",
    )
    parser.add_argument(
        "--ollama-base-url",
        default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        help="Base URL of Ollama API.",
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

    openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    model_router = ModelRouter(
        openai_client=openai_client,
        openai_model=args.openai_model,
        ollama_base_url=args.ollama_base_url,
        ollama_small_model=args.ollama_small_model,
        ollama_large_model=args.ollama_large_model,
    )

    server_command = args.mcp_command
    server_args = args.mcp_args if args.mcp_args is not None else [args.mcp_server]

    async with MCPToolClient(server_command=server_command, server_args=server_args) as mcp:
        tool_specs = await mcp.list_tools()
        orchestrator = AgentOrchestrator(
            model_router=model_router,
            mcp_client=mcp,
            tools=tool_specs,
            max_subagent_steps=args.max_subagent_steps,
        )
        final_answer = await orchestrator.run(prompt=args.prompt)
        print(final_answer)


if __name__ == "__main__":
    asyncio.run(main())
