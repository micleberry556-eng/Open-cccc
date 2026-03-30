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

## Quick Start

### Prerequisites
- Erlang/OTP 25+
- Elixir 1.14+
- Node.js 18+ (for asset compilation)

### Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/open_claw.git
cd open_claw

# Install dependencies
mix setup

# Set your API keys (optional - works without them)
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."

# Start the server
mix phx.server
```

Visit [`localhost:4000`](http://localhost:4000) to access the dashboard.

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Anthropic Claude API key | No |
| `OPENAI_API_KEY` | OpenAI API key | No |
| `GOOGLE_API_KEY` | Google Gemini API key | No |
| `OLLAMA_URL` | Ollama server URL (default: localhost:11434) | No |
| `SECRET_KEY_BASE` | Phoenix secret (required in prod) | Prod only |
| `PHX_HOST` | Production hostname | Prod only |
| `PORT` | HTTP port (default: 4000) | No |

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
