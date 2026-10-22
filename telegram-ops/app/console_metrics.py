"""Bounded process-local request timing. Never retain bodies, queries or identifiers."""
from collections import deque, defaultdict
from datetime import datetime, timezone
from math import ceil
from threading import Lock
from time import monotonic, perf_counter


class RequestMetrics:
    def __init__(self, capacity=1000, window_seconds=1800):
        self.samples = deque(maxlen=capacity)
        self.window_seconds = window_seconds
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.lock = Lock()

    def record(self, method, route, duration_ms, status):
        # route is the registered route template, not the request URL.
        if (not route or not route.startswith("/api/") or route == "/api/overview"
                or status in (401, 403) or method not in ("GET", "POST", "PUT", "DELETE", "PATCH")):
            return
        with self.lock:
            self.samples.append((monotonic(), method + " " + route, round(duration_ms, 1), status))

    def snapshot(self):
        with self.lock:
            cutoff = monotonic() - self.window_seconds
            while self.samples and self.samples[0][0] < cutoff:
                self.samples.popleft()
            samples = list(self.samples)
        grouped = defaultdict(list)
        for _, key, ms, status in samples:
            grouped[key].append((ms, status))
        rows = []
        for key, values in grouped.items():
            times = sorted(ms for ms, _ in values)
            rows.append(dict(route=key, count=len(times), errors=sum(s >= 400 for _, s in values),
                             p50_ms=times[ceil(len(times)*.5)-1], p95_ms=times[ceil(len(times)*.95)-1], max_ms=times[-1]))
        return dict(started_at=self.started_at, window_seconds=self.window_seconds, capacity=self.samples.maxlen,
                    samples=len(samples), rows=sorted(rows, key=lambda r: r["p95_ms"], reverse=True))


metrics = RequestMetrics()


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        started = perf_counter()
        status = 500

        async def timed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                # Only authenticated routes reach routing; leave rejected auth responses alone.
                if scope.get("route") and status not in (401, 403):
                    headers = list(message.get("headers", []))
                    headers.append((b"server-timing", f"app;dur={(perf_counter()-started)*1000:.1f}".encode()))
                    message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            metrics.record(scope["method"], getattr(scope.get("route"), "path", None), (perf_counter()-started)*1000, status)
