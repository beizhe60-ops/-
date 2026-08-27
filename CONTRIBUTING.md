# Contributing

Thanks for your interest in improving `telegram-ops`.

## Before you start

- Open an issue for substantial feature work or behavior changes
- Keep pull requests focused and easy to review
- Preserve existing user-visible behavior unless the change is intentional and documented

## Development setup

```bash
cd telegram-ops
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Pull request checklist

- Add or update tests when behavior changes
- Update documentation when configuration, workflows, or UI behavior changes
- Avoid committing real secrets, session files, or local databases
- Include screenshots when changing the admin panel UI

## Reporting bugs

When filing a bug, please include:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Logs or screenshots if available
- Deployment details such as Python version, database, and proxy configuration
