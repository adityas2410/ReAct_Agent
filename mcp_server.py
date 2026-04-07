from typing import Any
import csv
import os
import subprocess
import zipfile
from xml.sax.saxutils import escape
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

@mcp.tool()
def write_txt_file(path: str, content: str, append: bool = False) -> dict[str, Any]:
    """
    Creates or updates a UTF-8 text file.
    """
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    mode = "a" if append else "w"
    with open(abs_path, mode, encoding="utf-8") as file:
        file.write(content)
    return {
        "ok": True,
        "path": abs_path,
        "bytes_written": len(content.encode("utf-8")),
        "append": append,
    }

@mcp.tool()
def write_csv_file(path: str, rows: list[list[Any]], header: list[str] | None = None) -> dict[str, Any]:
    """
    Creates a CSV file from row data.
    """
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        if header:
            writer.writerow(header)
        for row in rows:
            writer.writerow(row)
    return {
        "ok": True,
        "path": abs_path,
        "row_count": len(rows),
        "has_header": bool(header),
    }

@mcp.tool()
def write_docx_file(path: str, title: str, paragraphs: list[str]) -> dict[str, Any]:
    """
    Creates a minimal .docx file without external dependencies.
    """
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)

    def to_paragraph_xml(text: str) -> str:
        return f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"

    title_xml = (
        "<w:p>"
        "<w:pPr><w:pStyle w:val=\"Title\"/></w:pPr>"
        f"<w:r><w:t>{escape(title)}</w:t></w:r>"
        "</w:p>"
    )
    body_xml = "".join(to_paragraph_xml(p) for p in paragraphs)

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {title_xml}
    {body_xml}
    <w:sectPr/>
  </w:body>
</w:document>"""

    with zipfile.ZipFile(abs_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)

    return {
        "ok": True,
        "path": abs_path,
        "paragraph_count": len(paragraphs),
        "title": title,
    }

if __name__ == "__main__":
    # Runs MCP server over stdio 
    mcp.run()
