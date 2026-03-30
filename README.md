# OpenClaw

**The open-source AI agent command center built with Elixir and Phoenix.**

OpenClaw is a ground-up rebuild of the agent orchestration concept in Elixir/Phoenix, leveraging the BEAM VM's native concurrency, fault tolerance, and real-time capabilities to create a fundamentally superior AI agent platform.

## Why Elixir?

The BEAM VM was designed for exactly this kind of workload -- massive concurrency, fault tolerance, and real-time communication:

- **Process-per-agent**: Each AI agent runs as a lightweight GenServer (~2KB). Run 100,000+ agents on a single node.
- **Fault isolation**: One agent crashing never takes down another. OTP Supervisors auto-restart failed agents.
- **Real-time by default**: Phoenix LiveView provides bidirectional WebSocket-based UI out of the box.
- **Hot code upgrades**: Update the platform without disconnecting running agents.
- **Built-in clustering**: Scale across nodes with `libcluster` -- agents can migrate between nodes.
- **Telemetry**: First-class observability with `:telemetry` and LiveDashboard.

## Features

### Mission Control Dashboard
- Real-time system metrics (memory, processes, schedulers)
- Agent activity monitoring with live status updates
- Cost tracking overview
- Quick actions for common operations

### Multi-Model Chat
- Anthropic Claude (Opus, Sonnet, Haiku)
- OpenAI GPT-4.1
- Google Gemini 2.5
- Ollama local models (Llama, Mistral, etc.)
- Switch models mid-conversation

### Agent Hub
- Spawn, pause, resume, and stop agents
- Per-agent cost and token tracking
- Real-time status updates via PubSub
- OTP-supervised with automatic crash recovery

### Integrated Terminal
- Full shell access with command history
- Built-in commands: `agents`, `system`, `help`
- Working directory tracking

### Cost Analytics
- Per-agent and per-provider cost breakdowns
- Token usage tracking
- Request history with timestamps
- Session cost summaries

### Skills Marketplace
- Extensible skill system
- Built-in skills: Web Search, Code Execution, File Manager, Browser Control
- Marketplace with community skills

## Architecture

```
                     OpenClaw (Phoenix App)
+----------------------------------------------------+
|                                                    |
|  LiveView UI          REST/WS API                  |
|  (Dashboard, Chat,    (External clients,           |
|   Terminal, etc.)      Mobile, CLI)                 |
|        |                    |                       |
|  +-----v--------------------v---------+            |
|  |         Phoenix.PubSub             |            |
|  |  (Real-time event bus)             |            |
|  +---------------+-------------------+             |
|                  |                                  |
|  +---------------v-------------------+             |
|  |     Agent Orchestration Layer     |             |
|  |  +----------+ +----------+       |             |
|  |  | Agent    | | Agent    | ...   |             |
|  |  | GenServer| | GenServer|       |             |
|  |  +----------+ +----------+       |             |
|  |       DynamicSupervisor           |             |
|  +-----------------------------------+             |
|                  |                                  |
|  +---------------v-------------------+             |
|  |  Services: Cost Tracker, Skills,  |             |
|  |  Gateway Manager, Cron Scheduler  |             |
|  +-----------------------------------+             |
+----------------------------------------------------+
```

## Quick Start (Docker — recommended)

The fastest way to run Nexora on any Linux server or PC. Docker and all
dependencies are installed automatically.

```bash
git clone https://github.com/micleberry556-eng/Open-cccc.git
cd Open-cccc

# One-command install: installs Docker, generates secrets, builds & starts.
chmod +x install.sh
sudo ./install.sh
```

Open [http://localhost:4000](http://localhost:4000) to access the dashboard.

After the first start, pull an LLM model for Ollama:

```bash
docker exec nexora-ollama ollama pull llama3
```

To stop / start later:

```bash
docker compose down          # stop
docker compose up -d         # start again
```

### Generate a Project with the Agent

Once Nexora is running and a model is pulled, use the sandbox to generate code:

```bash
# Generate a Python REST API from a task description
docker exec nexora-sandbox agent_runner --task "Build a REST API for a todo app with FastAPI"

# Generate from a task file
echo "Build a CLI calculator in Go with unit tests" > workspace/task.md
docker exec nexora-sandbox agent_runner --task-file /workspace/task.md --language go

# Specify max fix iterations
docker exec nexora-sandbox agent_runner --task "Snake game in Python with pygame" --max-iterations 10
```

Generated projects are saved to `./workspace/projects/`.

### Publish to GitHub

```bash
# Push a generated project to a GitHub repository
docker exec nexora-sandbox git_publish /workspace/projects/<project_name> https://github.com/user/repo.git

# With a custom branch
docker exec nexora-sandbox git_publish /workspace/projects/<project_name> https://github.com/user/repo.git dev
```

Set `GITHUB_TOKEN` in `.env` for automatic HTTPS authentication.

### Manual Docker Setup

If Docker is already installed:

```bash
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY_BASE:
#   openssl rand -base64 64 | tr -d '\n'
docker compose up -d --build
```

### Development Setup (without Docker)

Prerequisites: Erlang/OTP 25+, Elixir 1.14+, Node.js 18+.

```bash
mix setup
export SECRET_KEY_BASE="$(openssl rand -base64 64 | tr -d '\n')"
mix phx.server
```

Visit [`localhost:4000`](http://localhost:4000) to access the dashboard.

## Configuration

All settings are managed through the `.env` file (see `.env.example` for the
full list with descriptions). The `install.sh` script generates `.env`
automatically on first run.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY_BASE` | Phoenix secret for cookies/sessions | *generated* |
| `PHX_HOST` | Hostname for generated URLs | `localhost` |
| `NEXORA_PORT` | Port exposed on the host | `4000` |
| `LOG_LEVEL` | Logging level (debug/info/warning/error) | `info` |
| `MAX_AGENTS` | Max concurrent agent processes | `10` |
| `HEARTBEAT_INTERVAL_SEC` | Agent heartbeat interval (seconds) | `30` |
| `LLM_PROVIDER` | LLM backend: `local`, `ollama`, `openai` | `ollama` |
| `LLM_API_KEY` | API key for cloud LLM providers | — |
| `LLM_BASE_URL` | Custom LLM endpoint URL | *auto* |
| `LLM_MODEL` | Model name (llama3, mistral, codellama, ...) | `llama3` |
| `OLLAMA_PORT` | Ollama API port on the host | `11434` |
| `WORKSPACE_DIR` | Host directory mounted at `/workspace` | `./workspace` |
| `MAX_FIX_ITERATIONS` | Max generate-build-test-fix cycles | `5` |
| `GITHUB_TOKEN` | GitHub token for auto-publishing | — |
| `GIT_AUTHOR_NAME` | Git commit author name | `Nexora Agent` |
| `GIT_AUTHOR_EMAIL` | Git commit author email | `agent@nexora.local` |
| `BUDGET_LIMIT_USD` | Monthly budget cap (0 = unlimited) | `0` |
| `AUTH_ENABLED` | Require login for web UI | `false` |
| `ADMIN_USERNAME` | Admin login | `admin` |
| `ADMIN_PASSWORD` | Admin password | — |
| `ALLOWED_IPS` | Comma-separated IP allowlist | — |

### Included Services

| Service | Description |
|---------|-------------|
| **nexora** | Main application (Elixir/Phoenix) — dashboard, chat, terminal, agent management |
| **sandbox** | Isolated build environment with Python 3, Node.js 20, Go 1.22, git, and build tools |
| **ollama** | Local LLM server — runs models like Llama 3, Mistral, CodeLlama fully offline |

### How the Agent Works

```
Task (ТЗ) ──> LLM generates code ──> Build ──> Test
                                        |         |
                                        |    Errors?
                                        |     Yes ──> Send errors to LLM ──> Fix ──> Rebuild
                                        |     No  ──> Done! Project in /workspace/projects/
                                        |                    |
                                        |              git_publish ──> GitHub / GitLab / etc.
```

1. You describe what you want (task / ТЗ) in natural language.
2. The agent sends it to the local LLM (Ollama).
3. LLM generates all source files (code, tests, Makefile, README).
4. The sandbox builds and runs tests.
5. If errors are found, they are sent back to the LLM for automatic correction.
6. The cycle repeats until the build passes or max iterations are reached.
7. The finished project can be published to GitHub with one command.

### Workspace

The `./workspace` directory on the host is mounted into both the Nexora and
sandbox containers at `/workspace`. Generated projects go to
`/workspace/projects/`. All changes are immediately visible on the host.

### Supported Languages

The sandbox supports building and testing projects in:
- **Python** — pytest, black, ruff, mypy
- **JavaScript** — npm, jest, eslint, prettier
- **TypeScript** — tsc, ts-node, jest, eslint
- **Go** — go build, go test, goimports

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Elixir 1.14+ / OTP 25+ |
| Web Framework | Phoenix 1.7 |
| Real-time UI | Phoenix LiveView |
| Agent Runtime | GenServer + DynamicSupervisor |
| PubSub | Phoenix.PubSub (built-in) |
| Process Registry | Elixir Registry |
| Cost Tracking | ETS (in-memory) |
| CSS | Tailwind CSS |
| Deployment | Mix releases / Docker |

## Project Structure

```
lib/
  open_claw/
    application.ex              # OTP supervision tree
    runtime/
      agent_process.ex          # GenServer per agent
      agent_supervisor.ex       # DynamicSupervisor
      llm_client.ex             # Provider behaviour
      providers/
        anthropic.ex            # Claude integration
        openai.ex               # GPT integration
        google.ex               # Gemini integration
        ollama.ex               # Local model integration
    billing/
      cost_tracker.ex           # ETS-backed cost tracking
    skills/
      skill_registry.ex         # Skill management

  open_claw_web/
    live/
      dashboard_live.ex         # Main dashboard
      chat_live.ex              # Chat interface
      agents_live.ex            # Agent hub
      terminal_live.ex          # Terminal emulator
      analytics_live.ex         # Cost analytics
      skills_live.ex            # Skills marketplace
      settings_live.ex          # Configuration
    components/
      core_components.ex        # Shared UI components
      layouts/
        root.html.heex          # Root HTML layout
        app.html.heex           # App layout with sidebar
    router.ex                   # Route definitions
```

## Roadmap

- [x] Phase 1: Core foundation (Phoenix + Agent runtime + Chat)
- [x] Phase 2: Terminal, Analytics, Skills UI
- [ ] Phase 3: Distributed clustering with libcluster + Horde
- [ ] Phase 4: Multi-agent collaboration rooms
- [ ] Phase 5: Agent replay / event sourcing
- [ ] Phase 6: Multi-tenancy (schema-per-tenant)
- [ ] Phase 7: REST API with OpenAPI spec
- [ ] Phase 8: Webhook-driven missions

## Contributing

Contributions are welcome. Please open an issue first to discuss what you'd like to change.

## License

MIT License. See [LICENSE](LICENSE) for details.
