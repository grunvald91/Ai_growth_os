"""Shared delayed model failover; the ledger lives on the shared products volume.

A real pending request is the availability probe. A successful primary response
clears the outage. A fallback is permitted only after another failed primary
request at/after 24h. No sleeps or network calls while waiting for the next slot.
"""
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

WAIT_KIND = "seo_model_wait"
DAY = 24 * 60 * 60
HOUR = 60 * 60
TRANSIENT_STATUS = frozenset({404, 408, 425, 429, 500, 502, 503, 504})


class ModelDeferred(RuntimeError):
    def __init__(self, retry_at: float):
        super().__init__("Model unavailable: waiting for the next scheduled check")
        self.retry_at = retry_at


def transient(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS
    return isinstance(exc, (TimeoutError, httpx.TransportError))


def wait_until(deferred: ModelDeferred) -> str:
    return datetime.fromtimestamp(deferred.retry_at, timezone.utc).isoformat(timespec="microseconds")


def ready_filter(now=None):
    """SQL predicate: preserve the publication slot; only defer worker eligibility."""
    from sqlalchemy import exists, select
    from app.models.domain import Artifact, Task
    now = now or datetime.now(timezone.utc)
    return ~exists(select(Artifact.id).where(
        Artifact.product_id == Task.product_id, Artifact.task_id == Task.id,
        Artifact.kind == WAIT_KIND,
        Artifact.title > now.astimezone(timezone.utc).isoformat(timespec="microseconds"),
    ))


def defer_task(db, *, task, deferred):
    from app.models.domain import Artifact, TaskStatus
    from app.seo.activation import is_enabled
    task.status = TaskStatus.QUEUED if is_enabled(db, task.product_id) else TaskStatus.CANCELLED
    db.add(Artifact(product_id=task.product_id, task_id=task.id,
                    kind=WAIT_KIND, title=wait_until(deferred),
                    content=json.dumps({"retry_at": wait_until(deferred)})))


class AvailabilityPolicy:
    def __init__(self, root: Path, namespace: str, *, clock=None):
        self.root = Path(root) / ".seo-model-availability"
        self.namespace = namespace
        self.clock = clock or (lambda: datetime.now(timezone.utc).timestamp())

    def _key(self, model):
        return hashlib.sha256((self.namespace + "\0" + model).encode()).hexdigest()

    def _save(self, path, state):
        if state is None:
            path.unlink(missing_ok=True)
            return
        fd, temp = tempfile.mkstemp(dir=self.root, prefix=".state-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temp).unlink(missing_ok=True)

    @staticmethod
    def _load(path):
        if not path.exists():
            return None
        state = json.loads(path.read_text())
        if (not isinstance(state, dict) or set(state) != {"first", "last", "next"}
                or any(type(v) not in (int, float) or not 0 < v < 10**11 for v in state.values())
                or not state["first"] <= state["last"] < state["next"]):
            raise ValueError("Invalid SEO model availability state")
        return state

    async def generate(self, candidates, call):
        primary, *fallbacks = candidates
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = self._key(primary)
        path = self.root / (key + ".json")
        # One primary request per model at once across workers. Never wait
        # synchronously on a lock held during asynchronous provider IO.
        with open(self.root / (key + ".lock"), "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ModelDeferred(self.clock() + 60) from None
            state = self._load(path)
            now = self.clock()
            if state is None or now >= state["next"]:
                try:
                    result = await call(primary)
                except Exception as exc:
                    if not transient(exc):
                        # A response/configuration error is not evidence of
                        # continuous model unavailability. Restart the outage
                        # clock only on a subsequent genuine availability error.
                        self._save(path, None)
                        raise
                    now = self.clock()
                    first = min(state["first"], now) if state else now
                    age = now - first
                    interval = 300 if age < HOUR else 900
                    next_check = now + interval
                    if age < HOUR:
                        next_check = min(next_check, first + HOUR)
                    elif age < DAY:
                        next_check = min(next_check, first + DAY)
                    state = {"first": first, "last": now, "next": next_check}
                    self._save(path, state)
                else:
                    self._save(path, None)
                    return result
            # A clock reaching 24h alone is NOT sufficient. A real failed
            # primary probe at or beyond 24h must have been recorded.
            if state["last"] - state["first"] < DAY or not fallbacks:
                raise ModelDeferred(state["next"])
            retry_at = state["next"]
        # The reserve is itself a model, subject to exactly the same outage
        # cadence. Never jump straight from a newly unavailable reserve to
        # the third model. Keep the primary's next check in the schedule too.
        try:
            return await self.generate(tuple(fallbacks), call)
        except ModelDeferred as deferred:
            raise ModelDeferred(min(retry_at, deferred.retry_at)) from None


def policy_for(settings, *, seo=False, endpoint=None, provider=None):
    """All configured model names are eligible; no catalog allowlist or preflight approval."""
    if not (settings.llm_availability_enabled or (seo and settings.seo_model_availability_enabled)):
        return None
    provider = provider or settings.llm_provider
    endpoint = endpoint or (settings.xai_base_url if provider == "xai" else settings.anthropic_base_url)
    return AvailabilityPolicy(settings.products_root, provider + ":" + str(endpoint).rstrip("/"))
