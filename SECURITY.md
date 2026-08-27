# Security Policy

## Supported versions

Security fixes are expected to land on the default branch first.

## Reporting a vulnerability

Please do not open public issues for undisclosed vulnerabilities.

When reporting a security issue, include:

- A clear description of the problem
- Affected files, routes, or workflows
- Reproduction steps or a minimal proof of concept
- Impact and any suggested mitigation

If a private reporting channel is added later, this document should be updated to point to it.

## Deployment guidance

- Change `APP_SECRET_KEY` before deployment
- Prefer `ADMIN_PASSWORD_HASH` instead of a plain-text admin password
- Use HTTPS when exposing the admin panel over the public internet
- Restrict access with `ADMIN_ALLOWED_IPS` where possible
- Keep `.env`, session files, and databases out of version control
- Review automated reply rules before enabling outbound messaging
