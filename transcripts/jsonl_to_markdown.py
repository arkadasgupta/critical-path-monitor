"""Convert Claude Code JSONL transcripts into readable markdown."""

import json
import pathlib
import sys

MAX_RESULT = 2500
MAX_TOOL_INPUT = 1800
SKIP_TYPES = {
    "attachment", "file-history-snapshot", "file-history-delta",
    "queue-operation", "last-prompt", "atis-latch", "ai-title",
    "bridge-session",
}


def clip(text, limit):
    text = text.rstrip()
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


def fence(text, lang=""):
    # Avoid breaking out of the fence if the payload contains backticks.
    bt = "```"
    while bt in text:
        bt += "`"
    return f"{bt}{lang}\n{text}\n{bt}"


def render_tool_use(block, out):
    name = block.get("name", "?")
    inp = block.get("input", {}) or {}
    out.append(f"**→ {name}**\n")

    if name == "Bash" and "command" in inp:
        desc = inp.get("description")
        if desc:
            out.append(f"*{desc}*\n")
        body, cut = clip(str(inp["command"]), MAX_TOOL_INPUT)
        out.append(fence(body + ("\n… truncated" if cut else ""), "bash"))
        return

    if name in ("Write", "Edit", "Read", "NotebookEdit") and "file_path" in inp:
        out.append(f"`{inp['file_path']}`\n")
        payload = inp.get("content") or inp.get("new_string") or ""
        if payload:
            body, cut = clip(str(payload), MAX_TOOL_INPUT)
            out.append(fence(body + ("\n… truncated" if cut else "")))
        return

    if name in ("Agent", "Task"):
        if inp.get("description"):
            out.append(f"*{inp['description']}*\n")
        body, cut = clip(str(inp.get("prompt", "")), 4000)
        out.append(fence(body + ("\n… truncated" if cut else "")))
        return

    body, cut = clip(json.dumps(inp, indent=2, ensure_ascii=False), MAX_TOOL_INPUT)
    out.append(fence(body + ("\n… truncated" if cut else ""), "json"))


def result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for sub in content:
            if isinstance(sub, dict):
                if sub.get("type") == "text":
                    parts.append(sub.get("text", ""))
                elif sub.get("type") == "image":
                    parts.append("[image]")
            else:
                parts.append(str(sub))
        return "\n".join(parts)
    return "" if content is None else str(content)


def render(path, title, note):
    out = [f"# {title}\n", note, "\n---\n"]
    kept = 0

    for line in path.open():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") in SKIP_TYPES:
            continue

        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue

        content = message.get("content")
        blocks = content if isinstance(content, list) else [
            {"type": "text", "text": str(content)}
        ]

        rendered = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")

            if kind == "text":
                text = (block.get("text") or "").strip()
                # System reminders are harness plumbing, not conversation.
                if not text or text.startswith("<system-reminder>"):
                    continue
                rendered.append(text)

            elif kind == "tool_use":
                render_tool_use(block, rendered)

            elif kind == "tool_result":
                text = result_text(block).strip()
                if not text:
                    continue
                body, cut = clip(text, MAX_RESULT)
                suffix = f"\n… truncated ({len(text)} chars total)" if cut else ""
                rendered.append(
                    "<details><summary>result</summary>\n\n"
                    + fence(body + suffix)
                    + "\n</details>"
                )

            elif kind == "document":
                rendered.append("*[attached document omitted]*")

            # 'thinking' blocks are deliberately excluded; see the note above.

        if not rendered:
            continue

        stamp = (entry.get("timestamp") or "")[11:19]
        who = "User" if role == "user" else "Claude"
        # Guillemets so speaker headings stay distinguishable from any markdown
        # headings that appear inside the messages themselves.
        out.append("\n---\n")
        out.append(f"## \u2039{who}\u203a" + (f" · {stamp} UTC" if stamp else "") + "\n")
        out.append("\n\n".join(rendered))
        kept += 1

    return "\n".join(out) + "\n", kept


if __name__ == "__main__":
    src, dest, title, note = sys.argv[1:5]
    text, kept = render(pathlib.Path(src), title, note)
    pathlib.Path(dest).write_text(text)
    print(f"{dest}: {kept} messages, {len(text) // 1024} KB")
