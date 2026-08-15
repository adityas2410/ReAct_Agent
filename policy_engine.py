import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyResult:
    decision: str
    reason: str
    approval_status: str = "not_required"

    def to_trace(self) -> dict[str, str]:
        return {
            "policy_decision": self.decision,
            "policy_reason": self.reason,
            "approval_status": self.approval_status,
        }


class PolicyEngine:
    """
    Local governance layer for MCP tool calls.

    Decisions are intentionally simple: allow, approval_required, or deny.
    """

    def __init__(
        self,
        policy_path: str = "governance_policy.json",
        enabled: bool = True,
        auto_approve_safe_tools: bool = False,
    ) -> None:
        self.enabled = enabled
        self.auto_approve_safe_tools = auto_approve_safe_tools
        self.policy_path = Path(policy_path)
        self.policy = self._load_policy(self.policy_path)
        self.root_dir = self.policy_path.parent.resolve() if self.policy_path.parent != Path("") else Path.cwd().resolve()

    def review(self, tool_name: str, arguments: dict[str, Any], action_reason: str | None = None) -> PolicyResult:
        if not self.enabled:
            return PolicyResult(
                decision="allow",
                reason="Governance disabled by CLI flag.",
                approval_status="governance_disabled",
            )

        result = self.check(tool_name=tool_name, arguments=arguments)
        if result.decision != "approval_required":
            return result

        if self.auto_approve_safe_tools and tool_name in self.policy.get("safe_auto_approve_tools", []):
            return PolicyResult(
                decision="allow",
                reason=f"Auto-approved safe tool '{tool_name}'.",
                approval_status="auto_approved",
            )

        self._print_approval_request(tool_name=tool_name, arguments=arguments, reason=result.reason, action_reason=action_reason)
        try:
            answer = input("Approve this MCP tool call? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""

        if answer in {"y", "yes"}:
            return PolicyResult(
                decision="allow",
                reason=result.reason,
                approval_status="approved",
            )

        return PolicyResult(
            decision="deny",
            reason=f"User rejected MCP tool call '{tool_name}'.",
            approval_status="rejected",
        )

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PolicyResult:
        if tool_name == "email_process":
            return self._check_email(arguments)

        if tool_name in self.policy.get("file_write_tools", []):
            return self._check_file_write(tool_name=tool_name, arguments=arguments)

        if tool_name == "bash_execute":
            return self._check_bash(arguments)

        if tool_name in self.policy.get("allow_tools", []):
            return PolicyResult(decision="allow", reason=f"Tool '{tool_name}' is allowed by policy.")

        if tool_name in self.policy.get("approval_required_tools", []):
            return PolicyResult(decision="approval_required", reason=f"Tool '{tool_name}' requires user approval.")

        default_decision = str(self.policy.get("default_decision", "deny"))
        return PolicyResult(
            decision=default_decision,
            reason=f"Tool '{tool_name}' matched no explicit governance rule.",
        )

    def _check_email(self, arguments: dict[str, Any]) -> PolicyResult:
        mode = str(arguments.get("mode", "")).lower().strip()
        email_policy = self.policy.get("email_process", {})
        if mode in email_policy.get("allow_modes", []):
            return PolicyResult(decision="allow", reason=f"email_process mode '{mode}' is read-only/safe.")
        if mode in email_policy.get("approval_required_modes", []):
            return PolicyResult(decision="approval_required", reason=f"email_process mode '{mode}' requires approval.")
        return PolicyResult(
            decision=str(email_policy.get("default_decision", "approval_required")),
            reason=f"email_process mode '{mode or 'unspecified'}' is not explicitly safe.",
        )

    def _check_bash(self, arguments: dict[str, Any]) -> PolicyResult:
        command = str(arguments.get("command", ""))
        lowered = " ".join(command.lower().split())
        for pattern in self.policy.get("denied_command_patterns", []):
            if str(pattern).lower() in lowered:
                return PolicyResult(decision="deny", reason=f"Command contains denied pattern: {pattern}")

        cwd = arguments.get("cwd")
        if cwd and not self._is_allowed_path(str(cwd)):
            return PolicyResult(decision="deny", reason=f"bash_execute cwd is outside allowed workspace: {cwd}")

        return PolicyResult(decision="approval_required", reason="Shell execution requires user approval.")

    def _check_file_write(self, tool_name: str, arguments: dict[str, Any]) -> PolicyResult:
        path = arguments.get("path")
        if not path:
            return PolicyResult(decision="deny", reason=f"{tool_name} requires a path argument.")
        if not self._is_allowed_path(str(path)):
            return PolicyResult(decision="deny", reason=f"File write path is outside allowed workspace: {path}")
        return PolicyResult(decision="allow", reason=f"{tool_name} path is inside allowed workspace.")

    def _is_allowed_path(self, candidate: str) -> bool:
        candidate_path = Path(candidate)
        if not candidate_path.is_absolute():
            candidate_path = self.root_dir / candidate_path
        resolved_candidate = candidate_path.resolve(strict=False)

        for allowed in self.policy.get("workspace_dirs", []):
            allowed_path = Path(str(allowed))
            if not allowed_path.is_absolute():
                allowed_path = self.root_dir / allowed_path
            resolved_allowed = allowed_path.resolve(strict=False)
            if resolved_candidate == resolved_allowed or resolved_allowed in resolved_candidate.parents:
                return True
        return False

    def _load_policy(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return self._default_policy()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("Governance policy root must be a JSON object")
        return {**self._default_policy(), **loaded}

    def _default_policy(self) -> dict[str, Any]:
        return {
            "default_decision": "deny",
            "allow_tools": ["daily_summary"],
            "approval_required_tools": ["calendar_schedule", "social_post", "bash_execute"],
            "safe_auto_approve_tools": [],
            "email_process": {
                "allow_modes": ["summary", "urgent"],
                "approval_required_modes": ["cleanup"],
                "default_decision": "approval_required",
            },
            "file_write_tools": [
                "write_txt_file",
                "write_markdown_file",
                "write_csv_file",
                "write_json_file",
                "write_docx_file",
            ],
            "workspace_dirs": ["workspace", "outputs", "memory"],
            "denied_command_patterns": [
                "rm -rf",
                "del ",
                "format ",
                "shutdown",
                "curl | sh",
                "wget | sh",
            ],
        }

    def _print_approval_request(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        action_reason: str | None,
    ) -> None:
        print("\nGovernance approval required")
        print(f"Tool: {tool_name}")
        print(f"Policy reason: {reason}")
        if action_reason:
            print(f"Agent reason: {action_reason}")
        print(f"Arguments: {json.dumps(arguments, ensure_ascii=False, indent=2)}")
