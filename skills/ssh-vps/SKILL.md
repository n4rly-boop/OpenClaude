# Skill: ssh-vps

## Purpose
Run commands on the VPS via SSH using sshpass. Credentials are read from `.env` — never hardcoded.

## Usage

```bash
./skills/ssh-vps/run.sh "command"
```

### Examples
```bash
# Check disk usage
./skills/ssh-vps/run.sh "df -h"

# View running processes
./skills/ssh-vps/run.sh "ps aux | head -20"

# Read a file
./skills/ssh-vps/run.sh "cat /etc/os-release"

# Run multiple commands
./skills/ssh-vps/run.sh "uptime && free -h"
```

## Environment Variables (from .env)
- `VPS_HOST` — IP or hostname
- `VPS_PORT` — SSH port
- `VPS_USER` — SSH username
- `VPS_PASSWORD` — SSH password (via sshpass)

## Notes
- Uses `StrictHostKeyChecking=no` for convenience
- Connection timeout: 15s
- For interactive sessions, use sshpass directly from the terminal
