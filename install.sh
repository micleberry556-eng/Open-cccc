#!/usr/bin/env bash
# Nexora — automated installation script.
# Installs Docker, Docker Compose, generates secrets, and starts the application.
#
# Usage:
#   chmod +x install.sh
#   sudo ./install.sh
#
# Supported distributions: Ubuntu, Debian, Fedora, CentOS, RHEL, Arch Linux.

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

check_root() {
  if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (use sudo)."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Detect package manager
# ---------------------------------------------------------------------------

detect_distro() {
  if [ -f /etc/os-release ]; then
    # shellcheck source=/dev/null
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"
  else
    DISTRO_ID="unknown"
  fi
}

# ---------------------------------------------------------------------------
# Install Docker
# ---------------------------------------------------------------------------

install_docker() {
  if command -v docker &>/dev/null; then
    info "Docker is already installed: $(docker --version)"
    return
  fi

  info "Installing Docker..."

  case "$DISTRO_ID" in
    ubuntu|debian)
      apt-get update -y
      apt-get install -y ca-certificates curl gnupg lsb-release
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg
      echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/${DISTRO_ID} \
        $(lsb_release -cs) stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null
      apt-get update -y
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
      ;;
    fedora)
      dnf install -y dnf-plugins-core
      dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
      dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
      ;;
    centos|rhel|rocky|almalinux)
      yum install -y yum-utils
      yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
      ;;
    arch|manjaro)
      pacman -Sy --noconfirm docker docker-compose
      ;;
    *)
      error "Unsupported distribution: ${DISTRO_ID}. Install Docker manually: https://docs.docker.com/engine/install/"
      exit 1
      ;;
  esac

  systemctl enable --now docker
  info "Docker installed successfully: $(docker --version)"
}

# ---------------------------------------------------------------------------
# Install Docker Compose (standalone binary fallback)
# ---------------------------------------------------------------------------

install_compose() {
  # Docker Compose v2 ships as a plugin (docker compose).
  if docker compose version &>/dev/null; then
    info "Docker Compose plugin is available: $(docker compose version)"
    return
  fi

  # Fallback: install standalone docker-compose v2 binary.
  info "Installing Docker Compose standalone..."
  COMPOSE_VERSION="v2.29.1"
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64)  ARCH="x86_64" ;;
    aarch64) ARCH="aarch64" ;;
    *)       error "Unsupported architecture: ${ARCH}"; exit 1 ;;
  esac

  curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${ARCH}" \
    -o /usr/local/bin/docker-compose
  chmod +x /usr/local/bin/docker-compose
  info "Docker Compose installed: $(/usr/local/bin/docker-compose version)"
}

# ---------------------------------------------------------------------------
# Install curl (needed for healthcheck inside the container)
# ---------------------------------------------------------------------------

install_curl() {
  if command -v curl &>/dev/null; then
    return
  fi
  info "Installing curl..."
  case "$DISTRO_ID" in
    ubuntu|debian)       apt-get install -y curl ;;
    fedora)              dnf install -y curl ;;
    centos|rhel|rocky|almalinux) yum install -y curl ;;
    arch|manjaro)        pacman -Sy --noconfirm curl ;;
  esac
}

# ---------------------------------------------------------------------------
# Generate .env file
# ---------------------------------------------------------------------------

create_workspace() {
  local workspace_dir
  workspace_dir="$(cd "$(dirname "$0")" && pwd)/workspace"

  if [[ -d "$workspace_dir" ]]; then
    info "Workspace directory already exists: ${workspace_dir}"
    return
  fi

  info "Creating workspace directory for agent file access..."
  mkdir -p "$workspace_dir"
  mkdir -p "$workspace_dir/projects"
  chmod 777 "$workspace_dir"
  chmod 777 "$workspace_dir/projects"
  info "Workspace created at ${workspace_dir}"
}

generate_env() {
  local env_file
  env_file="$(cd "$(dirname "$0")" && pwd)/.env"

  if [[ -f "$env_file" ]]; then
    info ".env file already exists — skipping generation."
    return
  fi

  info "Generating .env file with a random SECRET_KEY_BASE..."

  SECRET_KEY_BASE="$(openssl rand -base64 64 | tr -d '\n')"

  cat > "$env_file" <<EOF
# ============================================================
# Nexora — Application Settings
# ============================================================

# Secret key used by Phoenix to sign cookies and sessions.
# Generated automatically — do not share publicly.
SECRET_KEY_BASE=${SECRET_KEY_BASE}

# Hostname that Phoenix will use in generated URLs.
PHX_HOST=localhost

# Port exposed on the host machine (the container always listens on 4000).
NEXORA_PORT=4000
EOF

  chmod 600 "$env_file"
  info ".env file created at ${env_file}"
}

# ---------------------------------------------------------------------------
# Build and start
# ---------------------------------------------------------------------------

start_app() {
  local project_dir
  project_dir="$(cd "$(dirname "$0")" && pwd)"

  info "Building and starting Nexora..."
  cd "$project_dir"
  docker compose up -d --build

  info "Waiting for Nexora to become healthy..."
  local retries=20
  while (( retries > 0 )); do
    if curl -sf http://localhost:"${NEXORA_PORT:-4000}"/ > /dev/null 2>&1; then
      echo ""
      info "============================================"
      info "  Nexora is running!"
      info "  Open http://localhost:${NEXORA_PORT:-4000}"
      info "============================================"
      echo ""
      info "Next steps:"
      info "  1. Pull an LLM model:  docker exec nexora-ollama ollama pull llama3"
      info "  2. Generate a project: docker exec nexora-sandbox agent_runner --task 'Your task here'"
      info "  3. Publish to GitHub:  docker exec nexora-sandbox git_publish /workspace/projects/<name> <repo_url>"
      return
    fi
    retries=$((retries - 1))
    sleep 3
    printf "."
  done

  echo ""
  warn "Nexora did not respond within 60 seconds."
  warn "Check logs with: docker compose logs -f"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  info "=== Nexora Installer ==="
  check_root
  detect_distro
  info "Detected distribution: ${DISTRO_ID}"

  install_curl
  install_docker
  install_compose
  create_workspace
  generate_env
  start_app

  info "Installation complete."
}

main "$@"
