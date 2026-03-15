# Hyve Deployment Guide

Complete guide to deploying, initializing, and operating the Hyve nanobot system on a DigitalOcean droplet with dual instances, Obsidian vault integration, and dedicated service accounts.

---

## Table of Contents

1. [Infrastructure Setup](#1-infrastructure-setup)
2. [Instance Configuration](#2-instance-configuration)
3. [Initialization & First Run](#3-initialization--first-run)
4. [Using Primary Features](#4-using-primary-features)
5. [Operations & Maintenance](#5-operations--maintenance)

---

## 1. Infrastructure Setup

### 1.1 Prerequisites

On your DigitalOcean droplet, ensure the following are installed:

```bash
# Docker + Docker Compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Tailscale (secure remote access)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Caddy (reverse proxy for HTTPS + auth)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy

# Git (for cloning the repo)
sudo apt install -y git
```

### 1.2 Clone & Build

```bash
cd /opt
git clone https://github.com/your-org/hyve.git
cd hyve

# Build the Docker image
docker build -t hyve-nanobot .

# Build the dashboard frontend (for serving via the dashboard command)
cd dashboard && npm install && npm run build && cd ..
```

### 1.3 Two-Instance Architecture

Hyve runs two isolated nanobot instances:

| Instance | Purpose | Port | Exposure |
|----------|---------|------|----------|
| `nanobot-personal` | Your personal assistant | 18790 | `localhost` only (Tailscale access) |
| `nanobot-symby` | Team/shared assistant | 18791 | Caddy HTTPS + basic auth |

Each instance gets its own config directory, EventStore, memory database, sessions, and dashboard.

Create the `docker-compose.yml` for the dual-instance setup:

```yaml
# /opt/hyve/docker-compose.prod.yml

x-common: &common
  image: hyve-nanobot
  restart: unless-stopped
  deploy:
    resources:
      limits:
        cpus: '1'
        memory: 1G
      reservations:
        cpus: '0.25'
        memory: 256M

services:
  # ── Personal instance (localhost only) ──────────────────
  nanobot-personal:
    <<: *common
    container_name: nanobot-personal
    command: ["gateway"]
    ports:
      - "127.0.0.1:18790:18790"
    environment:
      - NANOBOT_HOME=/root/.nanobot
    volumes:
      - ~/.nanobot-personal:/root/.nanobot
      - /path/to/obsidian-vault:/root/vault

  # ── Personal dashboard ──────────────────────────────────
  dashboard-personal:
    <<: *common
    container_name: dashboard-personal
    command: ["dashboard", "--host", "0.0.0.0", "--port", "18792", "--no-open"]
    ports:
      - "127.0.0.1:18792:18792"
    environment:
      - NANOBOT_HOME=/root/.nanobot
    volumes:
      - ~/.nanobot-personal:/root/.nanobot

  # ── Symby instance (team, exposed via Caddy) ───────────
  nanobot-symby:
    <<: *common
    container_name: nanobot-symby
    command: ["gateway", "--port", "18791"]
    ports:
      - "127.0.0.1:18791:18791"
    environment:
      - NANOBOT_HOME=/root/.nanobot
    volumes:
      - ~/.nanobot-symby:/root/.nanobot
      - /path/to/obsidian-vault:/root/vault

  # ── Symby dashboard ────────────────────────────────────
  dashboard-symby:
    <<: *common
    container_name: dashboard-symby
    command: ["dashboard", "--host", "0.0.0.0", "--port", "18793", "--no-open"]
    ports:
      - "127.0.0.1:18793:18793"
    environment:
      - NANOBOT_HOME=/root/.nanobot
    volumes:
      - ~/.nanobot-symby:/root/.nanobot
```

Start everything:

```bash
docker compose -f docker-compose.prod.yml up -d
```

### 1.4 Caddy Reverse Proxy

Create `/etc/caddy/Caddyfile` to expose Symby with HTTPS and basic auth:

```caddyfile
# Symby gateway — team access via Tailscale hostname or domain
symby.yourdomain.com {
    basicauth {
        # Generate hash: caddy hash-password --plaintext 'your-password'
        admin $2a$14$HASH_HERE
    }
    reverse_proxy localhost:18791
}

# Symby dashboard
dashboard.yourdomain.com {
    basicauth {
        admin $2a$14$HASH_HERE
    }
    reverse_proxy localhost:18793
}

# Personal dashboard (Tailscale-only access)
# Access via http://<tailscale-ip>:18792 directly, or:
personal.yourdomain.com {
    @tailscale remote_ip 100.64.0.0/10
    handle @tailscale {
        reverse_proxy localhost:18792
    }
    respond "Forbidden" 403
}
```

Reload Caddy:

```bash
sudo systemctl reload caddy
```

### 1.5 Tailscale Access

With Tailscale running, you can access the personal instance from any of your devices:

```bash
# From your laptop/phone (on the same Tailnet):
# Gateway API
curl http://<droplet-tailscale-ip>:18790/status

# Personal dashboard
open http://<droplet-tailscale-ip>:18792

# SSH for maintenance
ssh user@<droplet-tailscale-ip>
```

No ports exposed to the public internet for the personal instance.

---

## 2. Instance Configuration

### 2.1 Directory Structure

Each instance is fully isolated:

```
~/.nanobot-personal/          # Personal instance
├── config.json               # Provider keys, channel tokens, agent config
├── workspace/                # Agent workspace (files, sessions, cron)
│   ├── SOUL.md              # Personality definition
│   ├── AGENTS.md            # Agent instructions
│   ├── HEARTBEAT.md         # Periodic tasks
│   ├── USER.md              # User preferences
│   ├── MEMORY.md            # Generated knowledge index
│   ├── memory/              # Generated detail files
│   │   ├── people/
│   │   ├── projects/
│   │   ├── decisions/
│   │   └── context/
│   ├── sessions/            # Conversation history
│   └── cron/                # Scheduled job store
├── events.db                # EventStore (SQLite)
└── memory.db                # Memory system (SQLite)

~/.nanobot-symby/             # Team instance (same structure)
├── config.json
├── workspace/
├── events.db
└── memory.db
```

### 2.2 Config File Walkthrough

Initialize the config for each instance:

```bash
# Personal instance
NANOBOT_HOME=~/.nanobot-personal nanobot onboard

# Symby instance
NANOBOT_HOME=~/.nanobot-symby nanobot onboard
```

Then edit `~/.nanobot-personal/config.json`:

```jsonc
{
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot-personal/workspace",
      "model": "anthropic/claude-sonnet-4-20250514",
      "provider": "auto",
      "maxTokens": 8192,
      "temperature": 0.1,
      "maxToolIterations": 40,
      "memoryWindow": 100
    },
    // Named agents for multi-agent workflows
    "agents": {
      "coder": {
        "model": "anthropic/claude-sonnet-4-20250514",
        "systemPrompt": "You are a senior software engineer...",
        "tools": ["read_file", "write_file", "edit_file", "list_dir", "exec", "web_search"]
      },
      "researcher": {
        "model": "anthropic/claude-sonnet-4-20250514",
        "systemPrompt": "You are a research analyst...",
        "tools": ["web_search", "web_fetch", "read_file", "write_file"]
      },
      "writer": {
        "model": "anthropic/claude-sonnet-4-20250514",
        "systemPrompt": "You are a technical writer...",
        "tools": ["read_file", "write_file", "edit_file"]
      }
    },
    // Team chains for multi-agent coordination
    "teams": {
      "engineering": {
        "leader": "coder",
        "agents": ["coder", "researcher"],
        "approvalMode": "auto"
      }
    }
  },

  "providers": {
    "anthropic": {
      "apiKey": "sk-ant-..."
    },
    "openrouter": {
      "apiKey": "sk-or-..."
    }
    // Add others as needed: openai, deepseek, gemini, groq, etc.
  },

  "channels": {
    "sendProgress": true,
    "sendToolHints": false,
    "telegram": {
      "enabled": true,
      "token": "BOT_TOKEN_FROM_BOTFATHER",
      "allowFrom": ["YOUR_TELEGRAM_USER_ID"]
    },
    "discord": {
      "enabled": false,
      "token": "",
      "allowFrom": []
    },
    "email": {
      "enabled": true,
      "consentGranted": true,
      "imapHost": "127.0.0.1",         // Proton Bridge IMAP
      "imapPort": 1143,
      "imapUsername": "hyve@proton.me",
      "imapPassword": "bridge-password",
      "imapUseSsl": false,
      "smtpHost": "127.0.0.1",         // Proton Bridge SMTP
      "smtpPort": 1025,
      "smtpUsername": "hyve@proton.me",
      "smtpPassword": "bridge-password",
      "smtpUseTls": false,
      "smtpUseSsl": false,
      "fromAddress": "hyve@proton.me",
      "pollIntervalSeconds": 60,
      "allowFrom": []                   // Empty = accept all
    },
    "slack": {
      "enabled": false,
      "botToken": "xoxb-...",
      "appToken": "xapp-..."
    }
  },

  "gateway": {
    "port": 18790,
    "heartbeat": {
      "enabled": true,
      "intervalS": 1800               // 30 minutes
    }
  },

  "memory": {
    "enabled": true,
    "distillationModel": "anthropic/claude-sonnet-4-20250514",
    "vaultPath": "/root/vault",        // Obsidian vault path (mounted in Docker)
    "decay": {
      "stableTtlDays": 90,
      "activeTtlDays": 14,
      "sessionTtlHours": 24,
      "checkpointTtlHours": 4
    },
    "index": {
      "maxTokens": 3000,
      "activeContextSlots": 3
    },
    "schedule": {
      "hourlyPrune": true,
      "dailyDistillTime": "02:00",
      "weeklyCleanupDay": "sunday",
      "weeklyCleanupTime": "03:00"
    }
  },

  "tools": {
    "web": {
      "search": {
        "apiKey": "BSA_KEY_FOR_BRAVE_SEARCH"
      }
    },
    "exec": {
      "timeout": 60
    },
    "restrictToWorkspace": false,
    "mcpServers": {}
  }
}
```

### 2.3 Dedicated Service Accounts

Hyve uses dedicated accounts for its integrations. Configure these in the environment or via tools:

| Service | Account | Configuration |
|---------|---------|---------------|
| **GitHub** | `hyve-bot` (dedicated account) | Set PAT in env: `GH_TOKEN=ghp_...` or use `gh auth login` inside the container |
| **Email** | `hyve@proton.me` | Proton Bridge running on the droplet (IMAP/SMTP on localhost) |
| **Notion** | Hyve integration | API key in MCP server config or env |
| **Linear** | Hyve integration | API key in MCP server config or env |
| **Proton Drive** | `hyve@proton.me` | Mount via rclone or Proton Bridge |

For GitHub, ensure the PAT is available inside Docker:

```yaml
# In docker-compose.prod.yml, add to the service's environment:
environment:
  - GH_TOKEN=ghp_your_pat_here
  - GITHUB_TOKEN=ghp_your_pat_here
```

For Notion/Linear, the cleanest approach is MCP servers:

```jsonc
// In config.json → tools.mcpServers
"mcpServers": {
  "notion": {
    "command": "npx",
    "args": ["-y", "@notionhq/notion-mcp-server"],
    "env": { "NOTION_API_KEY": "ntn_..." }
  },
  "linear": {
    "command": "npx",
    "args": ["-y", "@linear/linear-mcp-server"],
    "env": { "LINEAR_API_KEY": "lin_..." }
  }
}
```

### 2.4 Obsidian Vault Integration

The Obsidian vault is where all memories, generated knowledge, and important outputs live. It should be a synced directory (e.g., via Obsidian Sync, Syncthing, or a git-backed vault).

```bash
# Example: vault is synced to /home/user/obsidian-vault on the droplet
# Map it into the container at /root/vault
```

Set `memory.vaultPath` in `config.json` to the container-internal path:

```json
"memory": {
  "enabled": true,
  "vaultPath": "/root/vault"
}
```

The memory system will generate these files in the vault:

```
/root/vault/
├── MEMORY.md                    ← Knowledge index (auto-generated)
├── memory/
│   ├── people/alice.md          ← Person detail files
│   ├── projects/hyve.md         ← Project detail files
│   ├── decisions/2026-03.md     ← Monthly decision log
│   └── context/current-sprint.md ← Active context
```

These files appear in Obsidian automatically and can be browsed, searched, and linked.

---

## 3. Initialization & First Run

### 3.1 Onboard

```bash
# Personal instance
docker exec -it nanobot-personal nanobot onboard

# Symby instance
docker exec -it nanobot-symby nanobot onboard
```

This creates the config file and workspace templates (`SOUL.md`, `AGENTS.md`, `HEARTBEAT.md`, `USER.md`, `TOOLS.md`).

### 3.2 Set API Keys

Edit `~/.nanobot-personal/config.json` on the host (it's volume-mounted into the container):

```bash
nano ~/.nanobot-personal/config.json
# Add your Anthropic/OpenRouter API key under providers
```

Verify:

```bash
docker exec -it nanobot-personal nanobot status
```

You should see your model and API key status listed.

### 3.3 Enable Channels

For each channel you want to enable, set `"enabled": true` and fill in the credentials in `config.json`.

**Telegram** (recommended — works great on mobile):
1. Message `@BotFather` on Telegram → `/newbot` → get the token
2. Set `channels.telegram.token` and `channels.telegram.allowFrom` (your user ID)
3. Restart the gateway: `docker restart nanobot-personal`

**WhatsApp** (requires QR code login):
```bash
docker exec -it nanobot-personal nanobot channels login
# Scan the QR code with WhatsApp
```

**Email** (Proton Bridge):
1. Install and run Proton Bridge on the droplet
2. Configure IMAP/SMTP settings in `channels.email`
3. Set `consentGranted: true`

Check channel status:
```bash
docker exec -it nanobot-personal nanobot channels status
```

### 3.4 Start the System

```bash
# Start all services
docker compose -f docker-compose.prod.yml up -d

# Check logs
docker logs -f nanobot-personal
```

You should see:
```
🐈 Starting nanobot gateway on port 18790...
✓ Channels enabled: telegram, email
✓ Cron: 0 scheduled jobs
✓ Heartbeat: every 1800s
✓ Memory system: scheduler + watcher enabled
✓ Multi-agent: agents=[coder, researcher, writer], teams=[engineering]
✓ Router: @agent, @team, #chain prefix dispatch enabled
```

### 3.5 Verify the Dashboard

```bash
# Demo mode (mock data, no running gateway needed)
docker exec -it nanobot-personal nanobot dashboard --demo --no-open --host 0.0.0.0

# Or visit via Tailscale
open http://<tailscale-ip>:18792
```

The dashboard shows:
- **TopBar** — connection status, agent/chain counts, total tokens
- **Sidebar** — agents, chains, teams
- **Chain Visualizer** — multi-agent flow diagrams
- **Event Feed** — live chronological event stream
- **Task Board** — Kanban view (Pending/Active/Done)
- **Work Log** — per-agent activity timeline
- **Command Bar** — send messages to the agent from the browser

### 3.6 Quick Test

```bash
# CLI test
docker exec -it nanobot-personal nanobot agent -m "Hello! What can you do?"

# Or message via Telegram if enabled
```

---

## 4. Using Primary Features

### 4.1 Chat (CLI, Channels, Dashboard)

**CLI — interactive mode:**
```bash
docker exec -it nanobot-personal nanobot agent
# Type messages, use /new for fresh session, /stop to cancel, /help for commands
```

**CLI — single message:**
```bash
docker exec -it nanobot-personal nanobot agent -m "Summarize the last 3 PRs on hyve"
```

**Channels:** Message the bot on Telegram, Discord, Slack, WhatsApp, or Email. The bot responds in-channel.

**Dashboard:** Type in the command bar at the bottom of the dashboard UI.

### 4.2 Multi-Agent Workflows

Use prefix dispatch to route messages to specific agents or teams:

```
@coder Refactor the auth module to use JWT tokens
@researcher Find the top 5 papers on RAG architectures from 2025
@writer Draft release notes for v0.2.0
```

**Team chains** coordinate multiple agents with approval gates:

```
#engineering Build a REST API for user management with tests
```

This creates a chain where the `coder` agent (team leader) can delegate to `researcher`, with automatic or manual approval depending on `approvalMode`.

**Chain lifecycle events** (`chain.delegated` → `chain.awaiting_approval` → `chain.completed`) appear on the dashboard's Chain Visualizer and Task Board.

### 4.3 Scheduled Tasks & Heartbeat

**Cron — via chat:**
```
Set a reminder to check deployment status every day at 9am
```
The agent uses the built-in `cron` tool to schedule the job.

**Heartbeat — via HEARTBEAT.md:**

Edit `workspace/HEARTBEAT.md` (via file tools or directly):

```markdown
# Heartbeat Tasks

## Morning Briefing
- Check GitHub notifications and summarize new issues
- Review email inbox for action items
- Post a morning summary to the team Slack channel

## Monitoring
- Check if the API endpoint https://api.example.com/health returns 200
- If any service is down, alert via Telegram
```

The heartbeat service checks this file every 30 minutes (configurable). If there are tasks, it sends them through the agent loop for execution.

**Managing cron jobs:**
```
List all my scheduled reminders
Remove the daily standup reminder
```

### 4.4 Memory System

The 4-layer memory architecture works automatically when `memory.enabled=true`:

| Layer | What | How |
|-------|------|-----|
| **1. Raw Events** | Daily notes, conversations, memory writes | Workspace watcher + conversation history |
| **2. Working Memory** | Session context, recent interactions | Managed by session manager |
| **3. Distilled Facts** | Structured facts with decay tiers | LLM extraction (daily at 02:00) |
| **4. Core Knowledge** | MEMORY.md + detail files | Template generation (after distillation) |

**Automatic operation:**
- Hourly: TTL pruning of expired facts
- Daily (02:00): Distillation (extract facts from events) + Generation (produce markdown files)
- Weekly (Sunday 03:00): Archive stale facts, compact event tables

**Manual CLI commands:**
```bash
# Check memory status
docker exec -it nanobot-personal nanobot memory status

# Run distillation manually
docker exec -it nanobot-personal nanobot memory distill

# Generate knowledge files
docker exec -it nanobot-personal nanobot memory generate

# Prune expired facts (dry run first)
docker exec -it nanobot-personal nanobot memory prune --dry-run
docker exec -it nanobot-personal nanobot memory prune
```

**Recall — ask the agent to remember:**
```
What do you remember about the hyve project?
What decisions did we make about the memory system?
Who is Alice and what's her role?
```

The agent uses the `recall` tool to search the facts database (FTS5 full-text search).

### 4.5 Dashboard

Launch the dashboard:
```bash
docker exec -it nanobot-personal nanobot dashboard --host 0.0.0.0 --port 18792 --no-open
```

Or access via the docker-compose setup at `http://<tailscale-ip>:18792`.

**Key panels:**

| Panel | What it shows |
|-------|---------------|
| **TopBar** | Connection status, agent count, chain count, total tokens used |
| **Sidebar** | Agent list (status indicators), chain list, team configs |
| **Chain Visualizer** | Flow diagram of multi-agent chains with node states |
| **Event Feed** | Live stream of all events (filterable by type) |
| **Task Board** | Kanban board — Pending / Active / Done tasks |
| **Work Log** | Per-agent timeline of iterations, tool calls, delegations |
| **Command Bar** | Send messages to the agent directly from the dashboard |

The dashboard connects via WebSocket for real-time updates. All events (agent lifecycle, tool calls, chain coordination, heartbeat, cron, usage tracking) stream live.

### 4.6 Skills

Built-in skills extend the agent's capabilities:

| Skill | What it does |
|-------|-------------|
| `github` | Interact with GitHub via `gh` CLI (issues, PRs, repos) |
| `weather` | Weather lookups via wttr.in and Open-Meteo |
| `summarize` | Summarize URLs, files, and YouTube videos |
| `tmux` | Control tmux sessions remotely |
| `cron` | Manage scheduled tasks |
| `memory` | Access and manage the layered memory system |
| `clawhub` | Search and install community skills |
| `skill-creator` | Create new custom skills |

Skills are loaded from `workspace/skills/` — each is a directory with a `SKILL.md` file containing instructions for the agent.

### 4.7 Cost Tracking

Every LLM call includes cost calculation based on the model pricing table. Costs are:
- Accumulated per-agent in `total_cost_usd`
- Included in `usage.tracked` events
- Visible in the dashboard TopBar and agent sidebar

```bash
# View events including cost data
docker exec -it nanobot-personal nanobot agent -m "How much have you spent today?"
```

---

## 5. Operations & Maintenance

### 5.1 Service Management

```bash
# Start/stop/restart
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml restart nanobot-personal

# View logs
docker logs -f nanobot-personal
docker logs -f nanobot-symby

# Shell access
docker exec -it nanobot-personal bash
```

### 5.2 Updating

```bash
cd /opt/hyve
git pull origin main

# Rebuild
docker build -t hyve-nanobot .
cd dashboard && npm run build && cd ..

# Restart
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

### 5.3 Backups

Critical data to back up:

| What | Path | Strategy |
|------|------|----------|
| Config | `~/.nanobot-personal/config.json` | Version control or automated backup |
| EventStore | `~/.nanobot-personal/events.db` | SQLite — copy or `sqlite3 .backup` |
| Memory DB | `~/.nanobot-personal/memory.db` | SQLite — copy or `sqlite3 .backup` |
| Sessions | `~/.nanobot-personal/workspace/sessions/` | File copy |
| Obsidian vault | `/path/to/obsidian-vault/` | Obsidian Sync / git / Syncthing |
| Workspace | `~/.nanobot-personal/workspace/` | File copy |

Backup script example:

```bash
#!/bin/bash
# /opt/hyve/backup.sh
BACKUP_DIR="/opt/backups/nanobot/$(date +%Y-%m-%d)"
mkdir -p "$BACKUP_DIR"

for instance in personal symby; do
    src="$HOME/.nanobot-$instance"
    dst="$BACKUP_DIR/$instance"
    mkdir -p "$dst"

    # SQLite safe backup
    sqlite3 "$src/events.db" ".backup '$dst/events.db'"
    sqlite3 "$src/memory.db" ".backup '$dst/memory.db'" 2>/dev/null || true

    # Config + workspace
    cp "$src/config.json" "$dst/"
    cp -r "$src/workspace" "$dst/"
done

echo "Backup complete: $BACKUP_DIR"
```

Add to cron: `0 3 * * * /opt/hyve/backup.sh`

### 5.4 Memory System Tuning

Adjust in `config.json → memory`:

| Setting | Default | What it controls |
|---------|---------|------------------|
| `decay.stableTtlDays` | 90 | How long "stable" facts live (project descriptions, etc.) |
| `decay.activeTtlDays` | 14 | How long "active" facts live (current tasks, sprint context) |
| `decay.sessionTtlHours` | 24 | How long session-scoped facts persist |
| `schedule.dailyDistillTime` | `02:00` | When daily distillation runs |
| `schedule.weeklyCleanupDay` | `sunday` | When weekly deep cleanup runs |
| `index.maxTokens` | 3000 | Token budget for MEMORY.md index |
| `index.activeContextSlots` | 3 | How many detail files are always loaded |

### 5.5 Monitoring

**Dashboard** — primary monitoring interface. Shows all agent activity, event streams, costs, and task progress in real-time.

**Event queries** — via the REST API:
```bash
# Recent events
curl http://localhost:18792/api/events?limit=20

# Filter by type
curl http://localhost:18792/api/events?type=agent.completed

# Current state
curl http://localhost:18792/api/state
```

**Health check:**
```bash
docker exec nanobot-personal nanobot status
```

### 5.6 Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot not responding on Telegram | Check `docker logs nanobot-personal` for channel errors. Verify token and allowFrom. |
| Memory distillation failing | Run `nanobot memory status` to check DB state. Ensure the LLM provider API key is valid. |
| Dashboard not connecting | Verify the dashboard container is running and WebSocket port is accessible. Check CORS settings. |
| High token usage | Check `nanobot memory status` for fact counts. Reduce `memoryWindow` or `maxToolIterations`. |
| Events not persisting | Check `events.db` is writable. Run `sqlite3 events.db "SELECT COUNT(*) FROM events"`. |
| Obsidian vault not updating | Verify the volume mount path in docker-compose. Run `nanobot memory generate` manually. |

---

## Quick Reference

```bash
# ── Instance management ──────────────────────────────
docker compose -f docker-compose.prod.yml up -d          # Start all
docker compose -f docker-compose.prod.yml down            # Stop all
docker restart nanobot-personal                           # Restart one

# ── Agent interaction ────────────────────────────────
docker exec -it nanobot-personal nanobot agent             # Interactive chat
docker exec -it nanobot-personal nanobot agent -m "Hello"  # Single message
docker exec -it nanobot-personal nanobot status             # System status

# ── Memory management ───────────────────────────────
docker exec -it nanobot-personal nanobot memory status      # DB stats
docker exec -it nanobot-personal nanobot memory distill     # Extract facts
docker exec -it nanobot-personal nanobot memory generate    # Build MEMORY.md
docker exec -it nanobot-personal nanobot memory prune       # Remove expired facts

# ── Dashboard ────────────────────────────────────────
open http://<tailscale-ip>:18792                           # Personal dashboard
open https://dashboard.yourdomain.com                      # Symby dashboard

# ── Channels ─────────────────────────────────────────
docker exec -it nanobot-personal nanobot channels status    # Channel health
docker exec -it nanobot-personal nanobot channels login     # WhatsApp QR
```
