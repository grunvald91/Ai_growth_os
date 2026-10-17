"""Private, request-bound stage checkpoints. Never automatically repeat a call.

An interrupted/failed paid call has unknown billing state and needs operator
reconciliation. This store is not a spending cap or persisted owner approval.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .contracts import WhiteboardRequest


class CheckpointError(RuntimeError):
    pass


class CheckpointStore:
    def __init__(self, directory: Path, request: WhiteboardRequest, *, models: dict[str, str], visual_review=False, image_provider=None):
        if image_provider not in (None, 'claudehub'):
            raise ValueError('Unsupported explicit image provider')
        if set(models) != {'storyboard', 'image', 'speech', 'regions'} or any(
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', value)
            or value.startswith(('sk-', 'sk_')) for value in models.values()
        ):
            raise ValueError('Four explicit public model IDs required')
        self.directory = Path(directory).absolute()
        for part in (self.directory, *self.directory.parents):
            if part.is_symlink():
                raise ValueError('Checkpoint directories must not contain symlinks')
        if self.directory.is_dir() and not (self.directory / 'manifest.json').exists() and any(self.directory.iterdir()):
            raise CheckpointError('Use a dedicated empty run directory')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.identity = {'request': request.model_dump(mode='json'), 'models': dict(models)}
        if visual_review:
            from .review import REVIEW_POLICY
            self.identity['visual_review'] = REVIEW_POLICY
        if image_provider:
            self.identity['image_provider'] = image_provider
        self.fingerprint = hashlib.sha256(json.dumps(self.identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self._lock():
            if self._path('manifest.json').exists():
                self._read_manifest()
            else:
                self._write_manifest({'schema_version': 1, 'fingerprint': self.fingerprint, **self.identity, 'stages': {}})

    def _path(self, name: str) -> Path:
        path = self.directory / name
        if path.is_symlink():
            raise CheckpointError('Symlink checkpoint file rejected')
        return path

    def _lock(self):
        return _Lock(self._path('run.lock'))

    def _atomic_write(self, path: Path, data: bytes) -> None:
        temporary = self.directory / ('.part-' + uuid4().hex)
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_manifest(self, state: dict) -> None:
        self._atomic_write(self._path('manifest.json'), json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode())

    def _read_manifest(self) -> dict:
        path = self._path('manifest.json')
        if path.stat().st_size > 64_000:
            raise CheckpointError('Manifest exceeds limit')
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            identity = {'request': state['request'], 'models': state['models']}
            if 'visual_review' in state:
                identity['visual_review'] = state['visual_review']
            if 'image_provider' in state:
                identity['image_provider'] = state['image_provider']
            actual = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if state['schema_version'] != 1 or state['fingerprint'] != self.fingerprint or actual != self.fingerprint or not isinstance(state['stages'], dict):
                raise ValueError('identity mismatch')
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointError('Invalid checkpoint identity; request/models changed') from exc
        return state

    def _cached(self, name: str, stage: dict, *, limit: int) -> bytes:
        if stage.get('status') != 'complete':
            raise CheckpointError('Prior call failed or was interrupted; billing is unknown. Automatic retry blocked')
        path = self._path(name + '.data')
        if not path.is_file() or path.stat().st_size > limit:
            raise CheckpointError('Missing or oversized cached stage')
        data = path.read_bytes()
        if len(data) != stage.get('size_bytes') or hashlib.sha256(data).hexdigest() != stage.get('sha256'):
            raise CheckpointError('Cached stage integrity check failed')
        return data

    def reset_unfinished(self) -> list[str]:
        """Forget failed/interrupted stages; only on an owner-confirmed resume."""
        with self._lock():
            state = self._read_manifest()
            names = [name for name, stage in state['stages'].items() if stage.get('status') != 'complete']
            for name in names:
                del state['stages'][name]
            if names:
                self._write_manifest(state)
            return names

    def load(self, name: str, *, limit: int = 12_000_000) -> bytes:
        self._validate_name(name)
        with self._lock():
            state = self._read_manifest()
            stage = state['stages'].get(name)
            if stage is None:
                raise CheckpointError('Stage has not completed')
            return self._cached(name, stage, limit=limit)

    def record_import(self, name, *, source_job_id, source_model_id):
        from uuid import UUID
        source_job_id = str(UUID(str(source_job_id)))
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', source_model_id):
            raise ValueError('Invalid imported source model ID')
        with self._lock():
            state = self._read_manifest()
            stage = state['stages'][name]
            self._cached(name, stage, limit=12_000_000)
            origin = {'source_job_id':source_job_id, 'source_model_id':source_model_id}
            if stage.get('imported_from') not in (None, origin):
                raise CheckpointError('Imported media provenance changed')
            stage['imported_from'] = origin
            self._write_manifest(state)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}', name):
            raise ValueError('Invalid stage name')

    async def _perform(self, name: str, callback, *, encode, decode, limit: int):
        self._validate_name(name)
        with self._lock():
            state = self._read_manifest()
            stage = state['stages'].get(name)
            if stage is not None:
                return decode(self._cached(name, stage, limit=limit))
            if len(state['stages']) >= 40:
                raise CheckpointError('Stage count limit exceeded')
            state['stages'][name] = {'status': 'running', 'cost_usd': None, 'billing_status': 'unknown'}
            self._write_manifest(state)
            try:
                value = await callback()
                data = encode(value)
                if not isinstance(data, bytes) or not 0 < len(data) <= limit:
                    raise ValueError('Invalid stage output size')
                checked = decode(data)
                self._atomic_write(self._path(name + '.data'), data)
                state['stages'][name].update(status='complete', size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                self._write_manifest(state)
                return checked
            except BaseException:
                state['stages'][name]['status'] = 'interrupted_or_failed'
                self._write_manifest(state)
                raise

    async def json_stage(self, name: str, callback, model):
        return await self._perform(name, callback, encode=lambda value: value.model_dump_json().encode(),
            decode=model.model_validate_json, limit=64_000)

    async def bytes_stage(self, name: str, callback, *, limit: int = 12_000_000):
        return await self._perform(name, callback, encode=lambda value: value, decode=lambda value: value, limit=limit)


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.descriptor = None

    def __enter__(self):
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.descriptor)
            self.descriptor = None
            raise CheckpointError('This run is already executing') from exc
        return self

    def __exit__(self, *args):
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
