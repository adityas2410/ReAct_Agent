from typing import Any
import os
import subprocess
import requests
from mcp.server.fastmcp import FastMCP

# Base URL where n8n exposes webhook triggers
N8N_WEBHOOK_BASE = "https://n8n-domain/webhook"

def call_n8n(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Sends a POST request to a specific n8n webhook.
    Each webhook corresponds to a single n8n workflow.
    """
    url = f"{N8N_WEBHOOK_BASE}/{path}"
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    return response.json() if response.content else {"status": "ok"}

# Create MCP server instance
mcp = FastMCP(name="n8n-automation-mcp")

@mcp.tool()
def email_process(mode: str, date: str | None = None) -> dict[str, Any]:
    """
    Triggers the n8n workflow responsible for email automation.
    Example modes: summary, urgent, cleanup
    """
    return call_n8n(
        "email-process",
        {
            "mode": mode,
            "date": date
        }
    )

@mcp.tool()
def calendar_schedule(title: str, date: str, time: str) -> dict[str, Any]:
    """
    Triggers the n8n workflow that creates calendar events.
    """
    return call_n8n(
        "calendar-schedule",
        {
            "title": title,
            "date": date,
            "time": time
        }
    )

@mcp.tool()
def social_post(platform: str, content: str) -> dict[str, Any]:
    """
    Triggers the n8n workflow for posting to social platforms.
    """
    return call_n8n(
        "social-post",
        {
            "platform": platform,
            "content": content
        }
    )

@mcp.tool()
def daily_summary() -> dict[str, Any]:
    """
    Triggers a daily automation workflow that can chain other workflows.
    """
    return call_n8n("daily-summary", {})

@mcp.tool()
def bash_execute(command: str, cwd: str | None = None, timeout_sec: int = 20) -> dict[str, Any]:
    """
    Executes a bash command and returns stdout/stderr/returncode.
    Useful for file navigation and local CLI automation tasks.
    """
    target_cwd = os.path.abspath(cwd or os.getcwd())
    if not os.path.isdir(target_cwd):
        return {
            "ok": False,
            "error": f"Invalid cwd: {target_cwd}",
            "command": command,
        }

    try:
        completed = subprocess.run(
            command,
            cwd=target_cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "command": command,
            "cwd": target_cwd,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "command": command,
            "cwd": target_cwd,
            "timed_out": True,
            "timeout_sec": timeout_sec,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }

if __name__ == "__main__":
    # Runs MCP server over stdio 
    mcp.run()
