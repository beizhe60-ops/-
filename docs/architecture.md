# Architecture Notes

## Stack

- Web framework: FastAPI
- Telegram client: Telethon
- ORM: SQLAlchemy
- Templates: Jinja2
- Database: SQLite by default

## Main components

- `app/main.py`: routes, admin authentication flow, dashboard, and web pages
- `app/workers.py`: background Telegram workers and raw update handling
- `app/rules.py`: rule matching, template rendering, lead creation, and queue logic
- `app/models.py`: SQLAlchemy models for accounts, chats, rules, leads, queue items, logs, and user guards

## Workflow

1. An admin adds and verifies a Telegram account.
2. The app syncs chats for that account.
3. A worker listens for incoming message updates.
4. Matched rules create lead records and optional queue items.
5. Operators review leads, queue state, and logs in the web UI.

## Current design strengths

- Small codebase that is easy to inspect
- Clear separation between routes, worker logic, and rule processing
- Built-in queue and guard concepts that make review workflows possible

## Current design limits

- Default deployment is single-node and SQLite-oriented
- Some operations are UI-driven rather than API-first
- Worker and queue behavior would benefit from broader automated test coverage
