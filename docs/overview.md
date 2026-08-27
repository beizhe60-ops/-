# Project Overview

`telegram-ops` is a self-hosted Telegram operations console built for teams that need visibility into message streams and a lightweight way to manage rule-driven workflows.

Core capabilities:

- Manage multiple Telegram accounts from a single admin panel
- Sync chats for each account and enable only the ones you want to monitor
- Match messages with keyword or regex rules
- Archive matched messages as leads
- Queue outbound actions with cooldown and daily-limit controls
- Review logs, queue state, and user guard records from the web UI

The current implementation is intentionally compact. It uses FastAPI for the web layer, Telethon for Telegram connectivity, SQLAlchemy for persistence, and SQLite by default for a simple deployment footprint.
