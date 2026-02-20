#!/usr/bin/env bash
# setup.sh — Interactive setup script for OpenClaude
# Checks prerequisites, configures .env, installs dependencies, sets up daemon.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "========================================"
echo "  OpenClaude Setup"
echo "========================================"
echo ""

# ── Prerequisite checks ──────────────────────────────────────────────

MISSING=0

echo "Checking prerequisites..."
echo ""

# Check python3
if command -v python3 &>/dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    echo "[OK] python3 found: $PYTHON_VERSION"
else
    echo "[MISSING] python3 is not installed"
    MISSING=1
fi

# Check pip
if command -v pip3 &>/dev/null || python3 -m pip --version &>/dev/null 2>&1; then
    echo "[OK] pip found"
else
    echo "[MISSING] pip is not installed (try: apt install python3-pip or brew install python3)"
    MISSING=1
fi

# Check claude CLI
if command -v claude &>/dev/null; then
    echo "[OK] claude CLI found"
else
    echo "[MISSING] claude CLI is not installed"
    echo "         Install: https://docs.anthropic.com/en/docs/claude-cli"
    MISSING=1
fi

echo ""

if [[ "$MISSING" -eq 1 ]]; then
    echo "Some prerequisites are missing. Please install them and re-run this script."
    exit 1
fi

echo "All prerequisites found."
echo ""

# ── Environment configuration ────────────────────────────────────────

ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE="$PROJECT_DIR/.env.example"

if [[ -f "$ENV_FILE" ]]; then
    echo ".env file already exists."
    read -rp "Overwrite with fresh configuration? [y/N] " OVERWRITE
    if [[ "$OVERWRITE" != "y" && "$OVERWRITE" != "Y" ]]; then
        echo "Keeping existing .env file."
    else
        CONFIGURE_ENV=1
    fi
else
    CONFIGURE_ENV=1
fi

if [[ "${CONFIGURE_ENV:-0}" -eq 1 ]]; then
    if [[ ! -f "$ENV_EXAMPLE" ]]; then
        echo "Error: .env.example not found at $ENV_EXAMPLE"
        exit 1
    fi

    echo ""
    echo "── Telegram Configuration ──"
    echo ""

    read -rp "Telegram Bot Token (from @BotFather): " BOT_TOKEN
    read -rp "Allowed Telegram User IDs (comma-separated): " ALLOWED_USERS
    read -rp "Default Chat ID for proactive messages (usually your user ID): " CHAT_ID

    echo ""
    echo "── Optional Configuration ──"
    echo ""

    read -rp "Deepgram API Key (for voice transcription, leave blank to skip): " DEEPGRAM_KEY

    read -rp "Claude model override (leave blank for default): " CLAUDE_MODEL

    # Write .env
    cp "$ENV_EXAMPLE" "$ENV_FILE"

    # Use python for reliable substitution
    python3 -c "
import re

with open('$ENV_FILE') as f:
    content = f.read()

replacements = {
    'TELEGRAM_BOT_TOKEN=': 'TELEGRAM_BOT_TOKEN=$BOT_TOKEN',
    'ALLOWED_USERS=': 'ALLOWED_USERS=$ALLOWED_USERS',
    'TELEGRAM_CHAT_ID=': 'TELEGRAM_CHAT_ID=$CHAT_ID',
    'DEEPGRAM_API_KEY=': 'DEEPGRAM_API_KEY=$DEEPGRAM_KEY',
    'CLAUDE_MODEL=': 'CLAUDE_MODEL=$CLAUDE_MODEL',
    'WORKING_DIR=': 'WORKING_DIR=$PROJECT_DIR',
}

for old, new in replacements.items():
    # Only replace the first exact line match
    content = content.replace(old, new, 1)

with open('$ENV_FILE', 'w') as f:
    f.write(content)
"

    echo ""
    echo ".env file created at $ENV_FILE"
fi

echo ""

# ── Install Python dependencies ──────────────────────────────────────

echo "Installing Python dependencies..."
if command -v pip3 &>/dev/null; then
    pip3 install -r "$PROJECT_DIR/requirements.txt"
else
    python3 -m pip install -r "$PROJECT_DIR/requirements.txt"
fi
echo ""
echo "Dependencies installed."
echo ""

# ── Dev branch setup ─────────────────────────────────────────────────

echo "Setting up git branches for safe deployment..."
cd "$PROJECT_DIR"

if git rev-parse --is-inside-work-tree &>/dev/null; then
    # Create dev branch if it doesn't exist
    if ! git rev-parse --verify dev &>/dev/null; then
        echo "  Creating 'dev' branch from 'main'..."
        git branch dev main 2>/dev/null || git branch dev HEAD
    else
        echo "  'dev' branch already exists."
    fi

    # Switch to dev and push to origin so remote tracking exists
    git checkout dev 2>/dev/null || echo "  (could not switch to dev — you may have uncommitted changes)"
    if git remote get-url origin &>/dev/null; then
        git push -u origin dev 2>/dev/null || echo "  (could not push dev to origin — push manually later)"
    fi

    # Seed known-good commit
    mkdir -p "$PROJECT_DIR/backups"
    KNOWN_GOOD="$PROJECT_DIR/backups/known-good-commit"
    if [[ ! -f "$KNOWN_GOOD" ]]; then
        git rev-parse HEAD > "$KNOWN_GOOD"
        echo "  Seeded known-good commit: $(git rev-parse --short HEAD)"
    else
        echo "  Known-good commit already set: $(cat "$KNOWN_GOOD" | head -c 7)"
    fi

    echo ""
    echo "  Workflow: the agent works on 'dev'. 'main' stays stable."
    echo "  safe-restart.sh syncs from origin/dev, runs tests, and rolls back on failure."
else
    echo "  Not a git repository — skipping branch setup."
fi
echo ""

# ── Make scripts executable ──────────────────────────────────────────

echo "Making scripts executable..."
find "$PROJECT_DIR" -name "*.sh" -exec chmod +x {} \;
echo "Done."
echo ""

# ── OS-specific daemon setup ─────────────────────────────────────────

OS="$(uname -s)"

if [[ "$OS" == "Darwin" ]]; then
    # macOS — launchd
    echo "Detected macOS."
    read -rp "Set up launchd daemons for Telegram bot and scheduled skills? [y/N] " SETUP_LAUNCHD

    if [[ "$SETUP_LAUNCHD" == "y" || "$SETUP_LAUNCHD" == "Y" ]]; then
        LAUNCHD_DIR="$PROJECT_DIR/services/launchd"
        PLIST_DEST="$HOME/Library/LaunchAgents"
        mkdir -p "$PLIST_DEST"

        # Replace placeholder paths in plist files
        for plist in "$LAUNCHD_DIR"/*.plist; do
            if [[ -f "$plist" ]]; then
                BASENAME="$(basename "$plist")"
                sed "s|/path/to/OpenClaude|$PROJECT_DIR|g" "$plist" > "$PLIST_DEST/$BASENAME"
                echo "  Installed: $PLIST_DEST/$BASENAME"
            fi
        done

        echo ""
        echo "Launchd plists installed. To activate:"
        echo ""
        for plist in "$PLIST_DEST"/com.claude.*.plist; do
            BASENAME="$(basename "$plist")"
            echo "  launchctl load $plist"
        done
        echo ""
        echo "To deactivate later:"
        echo "  launchctl unload ~/Library/LaunchAgents/com.claude.*.plist"
    fi

elif [[ "$OS" == "Linux" ]]; then
    # Linux — systemd
    echo "Detected Linux."
    read -rp "Set up systemd services for the Telegram bot? [y/N] " SETUP_SYSTEMD

    if [[ "$SETUP_SYSTEMD" == "y" || "$SETUP_SYSTEMD" == "Y" ]]; then
        SYSTEMD_DIR="$PROJECT_DIR/services/systemd"
        SYSTEMD_DEST="$HOME/.config/systemd/user"
        mkdir -p "$SYSTEMD_DEST"

        CURRENT_USER="$(whoami)"

        for unit in "$SYSTEMD_DIR"/*.service "$SYSTEMD_DIR"/*.timer; do
            if [[ -f "$unit" ]]; then
                BASENAME="$(basename "$unit")"
                sed -e "s|/path/to/OpenClaude|$PROJECT_DIR|g" \
                    -e "s|your-username-here|$CURRENT_USER|g" \
                    "$unit" > "$SYSTEMD_DEST/$BASENAME"
                echo "  Installed: $SYSTEMD_DEST/$BASENAME"
            fi
        done

        echo ""
        echo "Systemd units installed. To activate:"
        echo ""
        echo "  systemctl --user daemon-reload"
        for unit in "$SYSTEMD_DEST"/claude-*.service; do
            if [[ -f "$unit" ]]; then
                BASENAME="$(basename "$unit" .service)"
                echo "  systemctl --user enable --now $BASENAME"
            fi
        done
        echo ""
        echo "To check status:"
        echo "  systemctl --user status claude-telegram-bot"
    fi

    # Offer cron as alternative for heartbeat/daily-brief
    echo ""
    read -rp "Set up cron jobs for heartbeat and daily-brief? [y/N] " SETUP_CRON

    if [[ "$SETUP_CRON" == "y" || "$SETUP_CRON" == "Y" ]]; then
        echo ""
        echo "Add these lines to your crontab (crontab -e):"
        echo ""
        echo "  # OpenClaude daily brief — every day at 9:00 AM"
        echo "  0 9 * * * $PROJECT_DIR/skills/daily-brief/run.sh >> /tmp/claude-daily-brief.log 2>&1"
        echo ""
        echo "  # OpenClaude heartbeat — every 3 hours, 9 AM to 10 PM"
        echo "  0 */3 9-22 * * $PROJECT_DIR/skills/heartbeat/run.sh >> /tmp/claude-heartbeat.log 2>&1"
        echo ""
    fi
else
    echo "Unknown OS: $OS. Skipping daemon setup."
    echo "You can manually configure cron or another scheduler."
fi

# ── Done ─────────────────────────────────────────────────────────────

echo ""
echo "========================================"
echo "  Setup Complete"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "  1. Review your .env file:        $ENV_FILE"
echo "  2. Start the Telegram bot:       $PROJECT_DIR/bin/start.sh"
echo "  3. Message your bot on Telegram to verify it works"
echo "  4. (Optional) Enable daemon/cron for background operation"
echo "  5. (Optional) Customize BOOTSTRAP.md for new-chat identity setup"
echo ""
echo "For more info, see the project README or steps/ directory."
echo ""
