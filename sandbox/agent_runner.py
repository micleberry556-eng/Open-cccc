#!/usr/bin/env python3
"""Nexora Agent Runner — autonomous code generation, build, test, and fix loop.

Usage:
    agent_runner --task "Build a REST API for a todo app in Python with FastAPI"
    agent_runner --task-file /workspace/task.md
    agent_runner --task "CLI calculator in Go" --language go --max-iterations 10

The agent:
  1. Sends the task description (ТЗ) to the local LLM (Ollama).
  2. Parses the response and writes generated files to a project directory.
  3. Runs build and test commands appropriate for the detected language.
  4. If errors occur, sends them back to the LLM for correction.
  5. Repeats until the build and tests pass or max iterations are reached.
  6. Outputs the final project to /workspace/projects/<project_name>.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# ---------------------------------------------------------------------------

OLLAMA_URL = os.getenv("LLM_BASE_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
MAX_ITERATIONS = int(os.getenv("MAX_FIX_ITERATIONS", "5"))
PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "/workspace/projects"))
REQUEST_TIMEOUT = int(os.getenv("LLM_TIMEOUT_SEC", "300"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agent_runner")

# ---------------------------------------------------------------------------
# LLM interaction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert software engineer. The user will give you a task
    description (technical specification). You must produce ALL source files
    needed to build and run the project.

    Rules:
    - Return ONLY a JSON array of file objects. No markdown, no explanation.
    - Each object: {"path": "relative/file.py", "content": "...file content..."}
    - Include a README.md with build/run instructions.
    - Include tests when possible.
    - Include a Makefile or equivalent build script.
    - Use best practices for the chosen language.
""")

FIX_PROMPT_TEMPLATE = textwrap.dedent("""\
    The code you generated has errors. Here is the build/test output:

    ```
    {errors}
    ```

    Here are the current files:

    {files_json}

    Fix ALL errors and return the complete updated file list as a JSON array.
    Return ONLY the JSON array, no markdown, no explanation.
""")


def call_llm(messages: list[dict[str, str]]) -> str:
    """Send a chat completion request to Ollama and return the response text."""
    url = f"{OLLAMA_URL}/api/chat"
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 16384},
    }
    log.info("Calling LLM (%s) ...", LLM_MODEL)
    try:
        resp = httpx.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")
    except httpx.HTTPStatusError as exc:
        log.error("LLM HTTP error %s: %s", exc.response.status_code, exc.response.text[:500])
        raise
    except httpx.ConnectError:
        log.error("Cannot connect to LLM at %s. Is Ollama running?", OLLAMA_URL)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_files(raw: str) -> list[dict[str, str]]:
    """Extract the JSON file list from the LLM response.

    The LLM sometimes wraps JSON in markdown code fences — strip those first.
    """
    # Strip markdown fences if present.
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned.strip(), flags=re.MULTILINE)

    try:
        files = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find the first JSON array in the text.
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match:
            files = json.loads(match.group())
        else:
            log.error("Could not parse LLM response as JSON:\n%s", raw[:1000])
            return []

    if not isinstance(files, list):
        log.error("Expected a JSON array, got %s", type(files).__name__)
        return []

    valid: list[dict[str, str]] = []
    for entry in files:
        if isinstance(entry, dict) and "path" in entry and "content" in entry:
            valid.append({"path": str(entry["path"]), "content": str(entry["content"])})
    return valid


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def write_project(project_dir: Path, files: list[dict[str, str]]) -> None:
    """Write generated files to disk."""
    for f in files:
        fpath = project_dir / f["path"]
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(f["content"], encoding="utf-8")
        log.info("  wrote %s", f["path"])


def read_project_files(project_dir: Path) -> list[dict[str, str]]:
    """Read all files in the project directory back into the file-list format."""
    files: list[dict[str, str]] = []
    for fpath in sorted(project_dir.rglob("*")):
        if fpath.is_file() and not fpath.name.startswith("."):
            rel = str(fpath.relative_to(project_dir))
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            files.append({"path": rel, "content": content})
    return files


# ---------------------------------------------------------------------------
# Language detection and build/test commands
# ---------------------------------------------------------------------------

LANG_COMMANDS: dict[str, dict[str, list[str]]] = {
    "python": {
        "build": ["python3", "-m", "py_compile"],  # per-file; handled specially
        "test": ["python3", "-m", "pytest", "-x", "--tb=short", "-q"],
    },
    "javascript": {
        "build": ["npm", "install"],
        "test": ["npm", "test"],
    },
    "typescript": {
        "build": ["npm", "install"],
        "test": ["npx", "tsc", "--noEmit"],
    },
    "go": {
        "build": ["go", "build", "./..."],
        "test": ["go", "test", "./..."],
    },
}


def detect_language(files: list[dict[str, str]], hint: str | None = None) -> str:
    """Detect the primary language from file extensions or user hint."""
    if hint:
        hint_lower = hint.lower()
        for lang in LANG_COMMANDS:
            if lang in hint_lower:
                return lang
        # Common aliases.
        if hint_lower in ("js", "node"):
            return "javascript"
        if hint_lower in ("ts",):
            return "typescript"
        if hint_lower in ("py",):
            return "python"

    ext_counts: dict[str, int] = {}
    ext_map = {".py": "python", ".js": "javascript", ".ts": "typescript", ".go": "go"}
    for f in files:
        ext = Path(f["path"]).suffix
        lang = ext_map.get(ext)
        if lang:
            ext_counts[lang] = ext_counts.get(lang, 0) + 1

    if ext_counts:
        return max(ext_counts, key=lambda k: ext_counts[k])
    return "python"  # default


def run_command(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    """Run a shell command and return (returncode, combined output)."""
    log.info("  $ %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return 1, f"Command not found: {cmd[0]}"


def build_and_test(project_dir: Path, language: str) -> tuple[bool, str]:
    """Run build and test steps. Return (success, error_output)."""
    commands = LANG_COMMANDS.get(language, LANG_COMMANDS["python"])
    errors: list[str] = []

    # Special handling for Python: compile-check each .py file.
    if language == "python":
        for py_file in sorted(project_dir.rglob("*.py")):
            rc, out = run_command(
                ["python3", "-m", "py_compile", str(py_file)], cwd=project_dir
            )
            if rc != 0:
                errors.append(f"Compile error in {py_file.relative_to(project_dir)}:\n{out}")
    else:
        build_cmd = commands.get("build")
        if build_cmd:
            rc, out = run_command(build_cmd, cwd=project_dir)
            if rc != 0:
                errors.append(f"Build failed:\n{out}")
                return False, "\n\n".join(errors)

    # Run tests.
    test_cmd = commands.get("test")
    if test_cmd:
        rc, out = run_command(test_cmd, cwd=project_dir)
        if rc != 0:
            errors.append(f"Tests failed:\n{out}")

    if errors:
        return False, "\n\n".join(errors)
    return True, ""


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def generate_project_name(task: str) -> str:
    """Derive a short directory name from the task description."""
    words = re.sub(r"[^a-zA-Z0-9 ]", "", task).split()[:4]
    name = "_".join(w.lower() for w in words) or "project"
    return f"{name}_{int(time.time()) % 100000}"


def run(
    task: str,
    language_hint: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
    project_name: str | None = None,
) -> Path:
    """Execute the full generate -> build -> test -> fix loop."""
    pname = project_name or generate_project_name(task)
    project_dir = PROJECTS_DIR / pname
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)

    log.info("Project directory: %s", project_dir)
    log.info("Task: %s", task[:200])

    # ── Step 1: Initial generation ──────────────────────────────────────────
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    raw_response = call_llm(messages)
    files = parse_files(raw_response)

    if not files:
        log.error("LLM returned no parseable files. Aborting.")
        raise SystemExit(1)

    log.info("Generated %d files.", len(files))
    write_project(project_dir, files)

    language = detect_language(files, language_hint)
    log.info("Detected language: %s", language)

    # ── Step 2: Build / test / fix loop ─────────────────────────────────────
    for iteration in range(1, max_iterations + 1):
        log.info("── Iteration %d / %d ──", iteration, max_iterations)

        success, error_output = build_and_test(project_dir, language)
        if success:
            log.info("Build and tests passed!")
            break

        log.warning("Errors found. Sending to LLM for correction...")

        current_files = read_project_files(project_dir)
        files_json = json.dumps(current_files, indent=2, ensure_ascii=False)

        # Truncate error output to avoid exceeding context window.
        truncated_errors = error_output[:4000]

        fix_prompt = FIX_PROMPT_TEMPLATE.format(
            errors=truncated_errors, files_json=files_json
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
            {"role": "assistant", "content": raw_response},
            {"role": "user", "content": fix_prompt},
        ]
        raw_response = call_llm(messages)
        fixed_files = parse_files(raw_response)

        if not fixed_files:
            log.warning("LLM returned no parseable fix. Retrying...")
            continue

        log.info("Received %d fixed files.", len(fixed_files))
        write_project(project_dir, fixed_files)
    else:
        log.warning(
            "Reached max iterations (%d). Project may still have errors.", max_iterations
        )

    log.info("Project saved to %s", project_dir)
    return project_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nexora Agent Runner — generate, build, test, and fix code autonomously."
    )
    parser.add_argument(
        "--task", "-t", type=str, help="Task description / technical specification."
    )
    parser.add_argument(
        "--task-file", "-f", type=str, help="Path to a file containing the task description."
    )
    parser.add_argument(
        "--language", "-l", type=str, default=None, help="Language hint (python, go, javascript, typescript)."
    )
    parser.add_argument(
        "--max-iterations", "-m", type=int, default=MAX_ITERATIONS, help="Max fix iterations."
    )
    parser.add_argument(
        "--project-name", "-n", type=str, default=None, help="Custom project directory name."
    )
    args = parser.parse_args()

    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8").strip()
    elif args.task:
        task = args.task
    else:
        parser.error("Provide --task or --task-file.")
        return  # unreachable, keeps type checker happy

    project_dir = run(
        task=task,
        language_hint=args.language,
        max_iterations=args.max_iterations,
        project_name=args.project_name,
    )
    print(f"\nDone. Project at: {project_dir}")


if __name__ == "__main__":
    main()
