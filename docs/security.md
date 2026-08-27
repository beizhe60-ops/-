# Security Guidance

## Baseline hardening

- Change `APP_SECRET_KEY` before the first deployment
- Prefer `ADMIN_PASSWORD_HASH` over `ADMIN_PASSWORD`
- Expose the admin panel through HTTPS
- Use `ADMIN_ALLOWED_IPS` if the panel is internet-facing
- Keep the host firewall closed except for the ports you actively need

## Secrets and local state

Do not commit:

- Real `.env` files
- Telegram session artifacts
- Local database files
- Logs that contain message contents or user identifiers

Use `telegram-ops/.env.example` as the starting point for new deployments.

## Safer rollout sequence

1. Verify accounts and sync chats
2. Start with passive monitoring
3. Tune match rules and rate limits
4. Add human review to queue and guard flows
5. Enable outbound actions only after validating legal and policy requirements

## Operational review

Regularly review:

- Admin credentials
- Allowed IP ranges
- Queue backlog and failed sends
- Blacklist and unsubscribe guard records
- Reply templates for accidental disclosure or overreach
