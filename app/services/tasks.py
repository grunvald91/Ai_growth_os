from typing import Any
from uuid import UUID

from app.models import Task
from app.models.domain import TaskStatus
from app.services.base import CRUDService


class TaskService(CRUDService[Task]):
    model = Task
    update_fields = frozenset({"title", "description", "assigned_agent", "status", "priority", "due_at", "goal_id", "parent_id"})

    def create(self, *, product_id: UUID, title: str, **values: Any) -> Task:
        if "status" in values:
            values["status"] = self._status(values["status"])
        task = Task(product_id=product_id, title=title, **values)
        self.db.add(task)
        self.db.flush()
        self.db.refresh(task)
        return task

    def update(self, entity_id: UUID, **values: Any) -> Task:
        if "status" in values:
            values["status"] = self._status(values["status"])
        return self._update(entity_id, values)

    def list(self, *, product_id: UUID | None = None, status: TaskStatus | str | None = None, limit: int = 100, offset: int = 0) -> list[Task]:
        statement = self._list_statement(product_id)
        if status is not None:
            statement = statement.where(Task.status == self._status(status))
        return self._execute_list(statement, limit=limit, offset=offset)

    @staticmethod
    def _status(status: TaskStatus | str) -> TaskStatus:
        try:
            return status if isinstance(status, TaskStatus) else TaskStatus(status)
        except ValueError as exc:
            raise ValueError(f"Invalid task status: {status}") from exc
