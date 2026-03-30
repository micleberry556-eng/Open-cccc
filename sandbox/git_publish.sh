#!/usr/bin/env bash
# Nexora — publish a generated project to GitHub (or any Git remote).
#
# Usage:
#   git_publish <project_dir> <remote_url> [branch]
#   git_publish --create <project_dir> <repo_name> [branch]
#
# Examples:
#   git_publish /workspace/projects/todo_api https://github.com/user/todo-api.git
#   git_publish /workspace/projects/todo_api https://github.com/user/todo-api.git main
#   git_publish --create /workspace/projects/todo_api my-todo-api
#   git_publish --create /workspace/projects/todo_api my-todo-api dev
#
# Environment variables:
#   GIT_AUTHOR_NAME   — commit author name  (default: "Nexora Agent")
#   GIT_AUTHOR_EMAIL  — commit author email (default: "agent@nexora.local")
#   GIT_COMMIT_MSG    — custom commit message (default: auto-generated)
#   GITHUB_TOKEN      — required for --create; used for HTTPS auth on push

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

usage() {
  echo "Usage:"
  echo "  git_publish <project_dir> <remote_url> [branch]"
  echo "  git_publish --create <project_dir> <repo_name> [branch]"
  echo ""
  echo "Arguments:"
  echo "  project_dir   Path to the project directory to publish"
  echo "  remote_url    Git remote URL (HTTPS or SSH)"
  echo "  repo_name     Repository name to create on GitHub (with --create)"
  echo "  branch        Branch name (default: main)"
  echo ""
  echo "Flags:"
  echo "  --create      Create a new GitHub repository before pushing"
  echo ""
  echo "Environment variables:"
  echo "  GIT_AUTHOR_NAME    Commit author name  (default: Nexora Agent)"
  echo "  GIT_AUTHOR_EMAIL   Commit author email (default: agent@nexora.local)"
  echo "  GIT_COMMIT_MSG     Custom commit message"
  echo "  GITHUB_TOKEN       Token for HTTPS auth and repo creation"
  exit 1
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

CREATE_REPO=false
if [[ "${1:-}" == "--create" ]]; then
  CREATE_REPO=true
  shift
fi

if [[ $# -lt 2 ]]; then
  error "Missing required arguments."
  usage
fi

PROJECT_DIR="$1"
REMOTE_OR_NAME="$2"
BRANCH="${3:-main}"

AUTHOR_NAME="${GIT_AUTHOR_NAME:-Nexora Agent}"
AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-agent@nexora.local}"

if [[ ! -d "$PROJECT_DIR" ]]; then
  error "Project directory does not exist: ${PROJECT_DIR}"
  exit 1
fi

# ---------------------------------------------------------------------------
# Create GitHub repository (if --create)
# ---------------------------------------------------------------------------

if [[ "$CREATE_REPO" == true ]]; then
  REPO_NAME="$REMOTE_OR_NAME"

  if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    error "GITHUB_TOKEN is required for --create."
    echo "  Set it in .env or export it:  export GITHUB_TOKEN=ghp_..."
    exit 1
  fi

  info "Creating GitHub repository: ${REPO_NAME} ..."

  # Read description from report.json if available.
  DESCRIPTION=""
  REPORT_FILE="${PROJECT_DIR}/report.json"
  if [[ -f "$REPORT_FILE" ]] && command -v jq &>/dev/null; then
    DESCRIPTION=$(jq -r '.task // ""' "$REPORT_FILE" | head -c 200)
  fi

  CREATE_RESPONSE=$(curl -sf -X POST \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    https://api.github.com/user/repos \
    -d "{\"name\": \"${REPO_NAME}\", \"description\": \"${DESCRIPTION}\", \"private\": false, \"auto_init\": false}" \
    2>&1) || {
    error "Failed to create repository."
    echo "  Response: ${CREATE_RESPONSE}"
    echo ""
    echo "  Common causes:"
    echo "    - Repository already exists"
    echo "    - GITHUB_TOKEN lacks 'repo' scope"
    echo "    - Invalid repository name"
    exit 1
  }

  # Extract the clone URL from the response.
  if command -v jq &>/dev/null; then
    REMOTE_URL=$(echo "$CREATE_RESPONSE" | jq -r '.clone_url // ""')
    HTML_URL=$(echo "$CREATE_RESPONSE" | jq -r '.html_url // ""')
  else
    REMOTE_URL=$(echo "$CREATE_RESPONSE" | grep -o '"clone_url":"[^"]*"' | head -1 | cut -d'"' -f4)
    HTML_URL=$(echo "$CREATE_RESPONSE" | grep -o '"html_url":"[^"]*"' | head -1 | cut -d'"' -f4)
  fi

  if [[ -z "$REMOTE_URL" ]]; then
    error "Could not extract clone URL from GitHub response."
    exit 1
  fi

  info "Repository created: ${HTML_URL}"
else
  REMOTE_URL="$REMOTE_OR_NAME"
fi

# ---------------------------------------------------------------------------
# Inject GITHUB_TOKEN into HTTPS URL if provided
# ---------------------------------------------------------------------------

if [[ -n "${GITHUB_TOKEN:-}" ]] && [[ "$REMOTE_URL" == https://* ]]; then
  REMOTE_URL="${REMOTE_URL/https:\/\//https:\/\/${GITHUB_TOKEN}@}"
  info "Using GITHUB_TOKEN for authentication."
fi

# ---------------------------------------------------------------------------
# Initialize and push
# ---------------------------------------------------------------------------

cd "$PROJECT_DIR"

info "Project directory: ${PROJECT_DIR}"
info "Remote: $(echo "$REMOTE_URL" | sed 's|://[^@]*@|://***@|')"
info "Branch: ${BRANCH}"

# Initialize git repo if not already one.
if [[ ! -d ".git" ]]; then
  info "Initializing git repository..."
  git init -b "$BRANCH"
else
  info "Git repository already exists."
fi

# Configure author.
git config user.name "$AUTHOR_NAME"
git config user.email "$AUTHOR_EMAIL"

# Create .gitignore if missing.
if [[ ! -f ".gitignore" ]]; then
  cat > .gitignore <<'GITIGNORE'
# Python
__pycache__/
*.pyc
*.pyo
.venv/
*.egg-info/
dist/
build/

# Node.js
node_modules/
npm-debug.log

# Go
/vendor/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Nexora internals
.backups/
report.json
GITIGNORE
  info "Created default .gitignore"
fi

# Stage all files.
git add -A

# Determine commit message.
FILE_COUNT=$(git diff --cached --numstat | wc -l)
COMMIT_MSG="${GIT_COMMIT_MSG:-"Initial commit — ${FILE_COUNT} files generated by Nexora Agent"}"

info "Committing ${FILE_COUNT} files..."
git commit -m "$COMMIT_MSG"

# Add remote and push.
if git remote get-url origin &>/dev/null; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

info "Pushing to ${BRANCH}..."
if git push -u origin "$BRANCH" 2>&1; then
  echo ""
  info "============================================"
  info "  Published successfully!"
  if [[ -n "${HTML_URL:-}" ]]; then
    info "  URL: ${HTML_URL}"
  fi
  info "  Branch: ${BRANCH}"
  info "============================================"
else
  error "Push failed. Check your remote URL and credentials."
  echo ""
  echo "Troubleshooting:"
  echo "  - For HTTPS: set GITHUB_TOKEN environment variable"
  echo "  - For SSH:   ensure your SSH key is available in the container"
  echo "  - Verify the remote repository exists"
  exit 1
fi
