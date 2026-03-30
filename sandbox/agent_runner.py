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
    needed to build, test, and run the project.

    RESPONSE FORMAT — CRITICAL:
    Return ONLY a raw JSON array. No markdown fences, no explanation, no text
    before or after the JSON. The response must start with [ and end with ].

    Each element is an object with exactly two keys:
      {"path": "relative/path/to/file.py", "content": "...full file content..."}

    EXAMPLE (Python project):
    [
      {"path": "requirements.txt", "content": "fastapi>=0.100.0\\nuvicorn>=0.23.0\\npytest>=7.0.0\\n"},
      {"path": "main.py", "content": "from fastapi import FastAPI\\n\\napp = FastAPI()\\n\\n@app.get('/')\\ndef root():\\n    return {'status': 'ok'}\\n"},
      {"path": "test_main.py", "content": "from fastapi.testclient import TestClient\\nfrom main import app\\n\\nclient = TestClient(app)\\n\\ndef test_root():\\n    r = client.get('/')\\n    assert r.status_code == 200\\n"},
      {"path": "README.md", "content": "# My App\\n\\n## Install\\npip install -r requirements.txt\\n\\n## Run\\nuvicorn main:app\\n\\n## Test\\npytest\\n"}
    ]

    MANDATORY FILES by language:
    - Python:     requirements.txt (all dependencies with versions), tests (test_*.py)
    - JavaScript: package.json (with scripts.test defined), tests
    - TypeScript:  package.json (with scripts.test and tsconfig.json), tests
    - Go:         go.mod (with module name), *_test.go files

    RULES:
    - Every project MUST have tests. No exceptions.
    - Every project MUST have a README.md with install/run/test instructions.
    - Pin dependency versions (e.g., fastapi>=0.100.0, not just fastapi).
    - Use only well-known, stable libraries.
    - Write clean, well-commented code following best practices.
    - Do NOT use placeholder or stub implementations — write real, working code.
    - For Python: use if __name__ == "__main__" guard in entry points.
    - For Go: set module name to "project" in go.mod.
    - Escape special characters properly in JSON string values.
""")

FIX_PROMPT_TEMPLATE = textwrap.dedent("""\
    The code you generated has errors. Here is the build/test output:

    ```
    {errors}
    ```

    Here are the files that need fixing:

    {files_json}

    INSTRUCTIONS:
    1. Analyze each error carefully.
    2. Fix ALL errors — do not leave any unfixed.
    3. If a dependency is missing, add it to requirements.txt / package.json / go.mod.
    4. If a test fails, fix the code (not the test) unless the test itself is wrong.
    5. Return the COMPLETE updated file list as a raw JSON array.
    6. Include ALL files (both fixed and unchanged), not just the ones you changed.
    7. Return ONLY the JSON array — no markdown, no explanation.
""")

# ── Phase-based generation prompts ──────────────────────────────────────────

PLAN_PROMPT = textwrap.dedent("""\
    You are an expert software architect. The user will give you a task
    description. Create a detailed project plan.

    Return ONLY a raw JSON object (no markdown, no explanation). The object must
    have these keys:

    {
      "project_name": "short_name",
      "language": "python",
      "description": "One-line summary",
      "dependencies": ["fastapi>=0.100.0", "pytest>=7.0.0"],
      "files": [
        {
          "path": "main.py",
          "purpose": "Entry point — FastAPI app with routes"
        },
        {
          "path": "models.py",
          "purpose": "Pydantic models for request/response"
        },
        {
          "path": "test_main.py",
          "purpose": "Tests for all API endpoints"
        },
        {
          "path": "README.md",
          "purpose": "Install, run, and test instructions"
        }
      ]
    }

    RULES:
    - Always include a dependency file (requirements.txt / package.json / go.mod).
    - Always include test files.
    - Always include README.md.
    - Keep file count reasonable (3-10 files for most projects).
    - Each file must have a clear, specific purpose.
    - Use the most appropriate language for the task unless specified.
""")

FILE_GEN_PROMPT_TEMPLATE = textwrap.dedent("""\
    You are implementing file "{file_path}" for the following project.

    PROJECT PLAN:
    {plan_summary}

    FILES ALREADY WRITTEN:
    {existing_files}

    PURPOSE OF THIS FILE:
    {file_purpose}

    INSTRUCTIONS:
    - Write the COMPLETE content of this file. No placeholders, no stubs.
    - Make sure imports reference the other files in the project correctly.
    - Follow best practices for {language}.
    - Return ONLY the raw file content. No markdown fences, no explanation,
      no JSON wrapping. Just the code/text that goes into the file.
""")

SINGLE_FILE_FIX_PROMPT = textwrap.dedent("""\
    The file "{file_path}" has errors:

    ```
    {errors}
    ```

    Current content of the file:
    ```
    {file_content}
    ```

    Other project files for context: {other_files_list}

    Fix ALL errors in this file. Return ONLY the corrected file content.
    No markdown fences, no explanation — just the raw fixed code.
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


def preflight_check() -> None:
    """Verify that Ollama is reachable and the configured model is available.

    Exits with a clear message if something is wrong so the user doesn't have
    to debug cryptic HTTP errors mid-generation.
    """
    # 1. Check Ollama is reachable.
    log.info("Preflight: checking Ollama at %s ...", OLLAMA_URL)
    try:
        resp = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=15)
        resp.raise_for_status()
    except httpx.ConnectError:
        log.error(
            "Cannot connect to Ollama at %s.\n"
            "  Make sure the Ollama container is running:\n"
            "    docker compose up -d ollama\n"
            "  Then try again.",
            OLLAMA_URL,
        )
        raise SystemExit(1)
    except httpx.HTTPStatusError as exc:
        log.error("Ollama returned HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
        raise SystemExit(1)

    # 2. Check the model is pulled.
    data = resp.json()
    available_models: list[str] = []
    for m in data.get("models", []):
        name = m.get("name", "")
        available_models.append(name)
        # Ollama returns names like "llama3:latest"; match with or without tag.
        if name == LLM_MODEL or name.startswith(f"{LLM_MODEL}:"):
            log.info("Preflight: model '%s' is available.", LLM_MODEL)
            return

    if available_models:
        models_str = ", ".join(available_models)
        log.error(
            "Model '%s' is not downloaded. Available models: %s\n"
            "  Pull the model first:\n"
            "    docker exec nexora-ollama ollama pull %s\n"
            "  Then try again.",
            LLM_MODEL,
            models_str,
            LLM_MODEL,
        )
    else:
        log.error(
            "No models found in Ollama. Pull a model first:\n"
            "    docker exec nexora-ollama ollama pull %s\n"
            "  Then try again.",
            LLM_MODEL,
        )
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


def install_dependencies(project_dir: Path, language: str) -> tuple[bool, str]:
    """Install project dependencies before building/testing. Return (success, output)."""
    errors: list[str] = []

    if language == "python":
        req_file = project_dir / "requirements.txt"
        if req_file.exists():
            log.info("Installing Python dependencies...")
            rc, out = run_command(
                ["python3", "-m", "pip", "install", "-q", "-r", "requirements.txt"],
                cwd=project_dir,
            )
            if rc != 0:
                errors.append(f"pip install failed:\n{out}")
        # Also create a venv-less setup for imports to work.
        setup_py = project_dir / "setup.py"
        pyproject = project_dir / "pyproject.toml"
        if setup_py.exists() or pyproject.exists():
            rc, out = run_command(
                ["python3", "-m", "pip", "install", "-q", "-e", "."],
                cwd=project_dir,
            )
            if rc != 0:
                errors.append(f"pip install -e . failed:\n{out}")

    elif language == "go":
        go_mod = project_dir / "go.mod"
        if not go_mod.exists():
            log.info("Initializing Go module...")
            run_command(["go", "mod", "init", "project"], cwd=project_dir)
        log.info("Running go mod tidy...")
        rc, out = run_command(["go", "mod", "tidy"], cwd=project_dir)
        if rc != 0:
            errors.append(f"go mod tidy failed:\n{out}")

    elif language in ("javascript", "typescript"):
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            log.info("Installing Node.js dependencies...")
            rc, out = run_command(["npm", "install"], cwd=project_dir)
            if rc != 0:
                errors.append(f"npm install failed:\n{out}")

    if errors:
        return False, "\n\n".join(errors)
    return True, ""


def build_and_test(project_dir: Path, language: str) -> tuple[bool, str]:
    """Run build, lint, and test steps. Return (success, error_output)."""
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
        # Lint with ruff (fast, catches common issues).
        if not errors:
            rc, out = run_command(
                ["ruff", "check", "--select", "E,F,W", "--no-fix", "."],
                cwd=project_dir,
            )
            if rc != 0:
                errors.append(f"Lint errors (ruff):\n{out}")
        # Auto-format with black (non-blocking — just apply).
        run_command(["black", "--quiet", "."], cwd=project_dir)
    elif language == "go":
        build_cmd = commands.get("build")
        if build_cmd:
            rc, out = run_command(build_cmd, cwd=project_dir)
            if rc != 0:
                errors.append(f"Build failed:\n{out}")
                return False, "\n\n".join(errors)
        # go vet catches suspicious constructs.
        rc, out = run_command(["go", "vet", "./..."], cwd=project_dir)
        if rc != 0:
            errors.append(f"Lint errors (go vet):\n{out}")
    else:
        build_cmd = commands.get("build")
        if build_cmd:
            rc, out = run_command(build_cmd, cwd=project_dir)
            if rc != 0:
                errors.append(f"Build failed:\n{out}")
                return False, "\n\n".join(errors)
        # eslint for JS/TS (only if .eslintrc or eslint config exists, otherwise skip).
        if language in ("javascript", "typescript"):
            rc, out = run_command(
                ["npx", "eslint", "--no-eslintrc", "--rule", "{no-undef: error, no-unused-vars: warn}", "."],
                cwd=project_dir,
            )
            # eslint is advisory — don't block on it, just collect warnings.
            if rc != 0 and "error" in out.lower():
                errors.append(f"Lint errors (eslint):\n{out}")

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


def write_report(
    project_dir: Path,
    task: str,
    language: str,
    iterations: int,
    success: bool,
    final_errors: str,
) -> None:
    """Write a JSON report summarizing the generation result."""
    files = [str(p.relative_to(project_dir)) for p in sorted(project_dir.rglob("*")) if p.is_file()]
    report = {
        "project": str(project_dir),
        "task": task,
        "language": language,
        "iterations": iterations,
        "success": success,
        "files": files,
        "file_count": len(files),
        "errors": final_errors if not success else "",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    report_path = project_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Report written to %s", report_path)


# ---------------------------------------------------------------------------
# Plan-based generation (Phase 1 + Phase 2)
# ---------------------------------------------------------------------------


def parse_plan(raw: str) -> dict[str, Any]:
    """Parse the LLM plan response into a structured dict."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned.strip(), flags=re.MULTILINE)
    try:
        plan = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            plan = json.loads(match.group())
        else:
            log.error("Could not parse plan as JSON:\n%s", raw[:1000])
            return {}
    if not isinstance(plan, dict):
        return {}
    return plan


def generate_plan(task: str) -> dict[str, Any]:
    """Phase 1: Ask LLM to create a project architecture plan."""
    log.info("Phase 1: Generating project plan...")
    messages = [
        {"role": "system", "content": PLAN_PROMPT},
        {"role": "user", "content": task},
    ]
    raw = call_llm(messages)
    plan = parse_plan(raw)
    if not plan or "files" not in plan:
        log.warning("Plan generation failed, falling back to single-shot mode.")
        return {}
    log.info(
        "Plan: %s (%s) — %d files",
        plan.get("project_name", "?"),
        plan.get("language", "?"),
        len(plan.get("files", [])),
    )
    for f in plan.get("files", []):
        log.info("  - %s: %s", f.get("path", "?"), f.get("purpose", ""))
    return plan


def generate_file_by_plan(
    plan: dict[str, Any],
    file_entry: dict[str, str],
    project_dir: Path,
    language: str,
) -> str:
    """Phase 2: Generate a single file based on the plan and existing files."""
    # Build a summary of already-written files (names + first few lines).
    existing: list[str] = []
    for fpath in sorted(project_dir.rglob("*")):
        if fpath.is_file() and not fpath.name.startswith("."):
            rel = str(fpath.relative_to(project_dir))
            try:
                preview = fpath.read_text(encoding="utf-8", errors="replace")[:500]
            except Exception:
                preview = "(unreadable)"
            existing.append(f"--- {rel} ---\n{preview}\n")

    existing_str = "\n".join(existing) if existing else "(none yet)"
    plan_summary = json.dumps(
        {
            "project_name": plan.get("project_name", ""),
            "description": plan.get("description", ""),
            "language": plan.get("language", language),
            "dependencies": plan.get("dependencies", []),
            "files": [f.get("path", "") for f in plan.get("files", [])],
        },
        indent=2,
    )

    prompt = FILE_GEN_PROMPT_TEMPLATE.format(
        file_path=file_entry.get("path", ""),
        plan_summary=plan_summary,
        existing_files=existing_str,
        file_purpose=file_entry.get("purpose", ""),
        language=language,
    )
    messages = [
        {"role": "system", "content": "You are an expert software engineer. Write clean, working code."},
        {"role": "user", "content": prompt},
    ]
    raw = call_llm(messages)
    # Strip markdown fences if the LLM wraps the response.
    content = re.sub(r"^```\w*\s*\n?", "", raw.strip(), flags=re.MULTILINE)
    content = re.sub(r"\n?```\s*$", "", content.strip(), flags=re.MULTILINE)
    return content


def validate_single_file(file_path: Path, language: str) -> tuple[bool, str]:
    """Quick syntax check for a single file. Return (ok, error_msg)."""
    if language == "python" and file_path.suffix == ".py":
        rc, out = run_command(
            ["python3", "-m", "py_compile", str(file_path)],
            cwd=file_path.parent,
        )
        if rc != 0:
            return False, out
    elif language == "go" and file_path.suffix == ".go":
        # go vet on a single file isn't practical; skip per-file for Go.
        pass
    elif language in ("javascript", "typescript") and file_path.suffix in (".js", ".ts"):
        if file_path.suffix == ".ts":
            rc, out = run_command(
                ["npx", "tsc", "--noEmit", "--allowJs", str(file_path)],
                cwd=file_path.parent,
            )
            if rc != 0:
                return False, out
    return True, ""


def fix_single_file(
    file_path: Path,
    error_msg: str,
    project_dir: Path,
    language: str,
    max_retries: int = 3,
) -> bool:
    """Try to fix a single file up to max_retries times. Return True if fixed."""
    rel = str(file_path.relative_to(project_dir))
    other_files = [
        str(p.relative_to(project_dir))
        for p in sorted(project_dir.rglob("*"))
        if p.is_file() and p != file_path and not p.name.startswith(".")
    ]

    for attempt in range(1, max_retries + 1):
        log.info("  Fixing %s (attempt %d/%d)...", rel, attempt, max_retries)
        content = file_path.read_text(encoding="utf-8", errors="replace")
        prompt = SINGLE_FILE_FIX_PROMPT.format(
            file_path=rel,
            errors=error_msg[:2000],
            file_content=content[:4000],
            other_files_list=", ".join(other_files),
        )
        messages = [
            {"role": "system", "content": "You are an expert software engineer. Fix the code."},
            {"role": "user", "content": prompt},
        ]
        raw = call_llm(messages)
        fixed = re.sub(r"^```\w*\s*\n?", "", raw.strip(), flags=re.MULTILINE)
        fixed = re.sub(r"\n?```\s*$", "", fixed.strip(), flags=re.MULTILINE)
        file_path.write_text(fixed, encoding="utf-8")

        ok, new_err = validate_single_file(file_path, language)
        if ok:
            log.info("  Fixed %s successfully.", rel)
            return True
        error_msg = new_err

    log.warning("  Could not fix %s after %d attempts.", rel, max_retries)
    return False


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

    # ── Preflight: verify Ollama + model before doing anything ──────────────
    preflight_check()

    # ── Step 1: Try plan-based generation (Phase 1 + Phase 2) ──────────────
    plan = generate_plan(task)
    language = language_hint or plan.get("language", "python")

    if plan and plan.get("files"):
        log.info("Using plan-based generation (file-by-file)...")

        # Write dependency file first if specified in the plan.
        deps = plan.get("dependencies", [])
        if deps and language == "python":
            req_path = project_dir / "requirements.txt"
            req_path.write_text("\n".join(deps) + "\n", encoding="utf-8")
            log.info("  wrote requirements.txt (%d deps)", len(deps))

        # Generate each file individually, validate after each one.
        for file_entry in plan["files"]:
            fpath_str = file_entry.get("path", "")
            if not fpath_str:
                continue
            # Skip dependency files we already wrote.
            if fpath_str == "requirements.txt" and (project_dir / fpath_str).exists():
                continue

            log.info("Phase 2: Generating %s ...", fpath_str)
            content = generate_file_by_plan(plan, file_entry, project_dir, language)

            fpath = project_dir / fpath_str
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
            log.info("  wrote %s", fpath_str)

            # Validate immediately after writing.
            ok, err = validate_single_file(fpath, language)
            if not ok:
                log.warning("  Syntax error in %s, attempting fix...", fpath_str)
                fix_single_file(fpath, err, project_dir, language)

        # Detect language from actual files if hint wasn't given.
        actual_files = read_project_files(project_dir)
        if not language_hint:
            language = detect_language(actual_files, language_hint)
    else:
        # Fallback: single-shot generation (original behavior).
        log.info("Using single-shot generation...")
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

    # ── Step 2: Install dependencies & build / test / fix loop ────────────
    deps_installed = False
    final_success = False
    final_errors = ""
    iterations_used = 0
    for iteration in range(1, max_iterations + 1):
        iterations_used = iteration
        log.info("── Iteration %d / %d ──", iteration, max_iterations)

        # Install dependencies once, and again after each fix (new deps may appear).
        if not deps_installed or iteration > 1:
            dep_ok, dep_err = install_dependencies(project_dir, language)
            if not dep_ok:
                log.warning("Dependency install issues:\n%s", dep_err)
            deps_installed = True

        success, error_output = build_and_test(project_dir, language)
        if success:
            log.info("Build and tests passed!")
            final_success = True
            break

        final_errors = error_output

        log.warning("Errors found. Sending to LLM for correction...")

        current_files = read_project_files(project_dir)

        # Smart context: extract filenames mentioned in errors and send only
        # those files in full. Include a brief listing of other files so the
        # LLM knows the project structure but doesn't blow the context window.
        error_mentioned_files: set[str] = set()
        for f in current_files:
            if f["path"] in error_output:
                error_mentioned_files.add(f["path"])

        focused_files: list[dict[str, str]] = []
        other_file_names: list[str] = []
        for f in current_files:
            if f["path"] in error_mentioned_files or f["path"].endswith(
                ("requirements.txt", "package.json", "go.mod", "go.sum")
            ):
                focused_files.append(f)
            else:
                other_file_names.append(f["path"])

        # If no specific files were identified, fall back to sending all files
        # but truncate large ones to keep context manageable.
        if not focused_files:
            max_content_len = 3000
            for f in current_files:
                truncated = {
                    "path": f["path"],
                    "content": f["content"][:max_content_len]
                    + ("\n... (truncated)" if len(f["content"]) > max_content_len else ""),
                }
                focused_files.append(truncated)
            other_file_names = []

        files_json = json.dumps(focused_files, indent=2, ensure_ascii=False)
        if other_file_names:
            files_json += f"\n\n(Other unchanged files: {', '.join(other_file_names)})"

        # Truncate error output to avoid exceeding context window.
        truncated_errors = error_output[:4000]

        fix_prompt = FIX_PROMPT_TEMPLATE.format(
            errors=truncated_errors, files_json=files_json
        )
        # Don't include the previous raw_response — it's large and the files
        # already contain the current state. Keep the conversation short.
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Original task: {task}"},
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

    # ── Step 3: Write report ────────────────────────────────────────────────
    write_report(project_dir, task, language, iterations_used, final_success, final_errors)

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
