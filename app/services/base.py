from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


class CRUDService(Generic[ModelT]):
    model: type[ModelT]
    update_fields: frozenset[str]

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, entity_id: UUID) -> ModelT | None:
        return self.db.get(self.model, entity_id)

    def _list_statement(self, product_id: UUID | None) -> Select[tuple[ModelT]]:
        statement: Select[tuple[ModelT]] = select(self.model)
        if product_id is not None:
            statement = statement.where(self.model.product_id == product_id)
        return statement

    def _execute_list(self, statement: Select[tuple[ModelT]], *, limit: int, offset: int) -> list[ModelT]:
        if limit < 1 or limit > 1000 or offset < 0:
            raise ValueError("limit must be between 1 and 1000 and offset cannot be negative")
        statement = statement.order_by(self.model.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.scalars(statement))

    def list(self, *, product_id: UUID | None = None, limit: int = 100, offset: int = 0) -> list[ModelT]:
        return self._execute_list(self._list_statement(product_id), limit=limit, offset=offset)

    def _update(self, entity_id: UUID, values: dict[str, Any]) -> ModelT:
        entity = self.get(entity_id)
        if entity is None:
            raise ValueError(f"{self.model.__name__} {entity_id} was not found")
        unknown = set(values) - self.update_fields
        if unknown:
            raise ValueError(f"Unsupported fields: {', '.join(sorted(unknown))}")
        for key, value in values.items():
            setattr(entity, key, value)
        self.db.flush()
        self.db.refresh(entity)
        return entity
