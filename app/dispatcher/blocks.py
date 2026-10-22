"""Answer blocks shown in the owner's feed.

The server builds links, numbers and tables from the registry and the database. The model only
supplies words, so a wrong link or invented figure cannot reach the screen. A block that fails
validation is dropped; the owner always still has the text.
"""
from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

# Pages outside Studio that a link block may open. Everything else must be a Studio path.
EXTERNAL_HOSTS = frozenset({"servgo.ru", "growth.servgo.ru"})


def safe_href(href: str) -> bool:
    if href.startswith("/studio") or href.startswith("/intelligence"):
        return "//" not in href.split("?", 1)[0] and "\\" not in href
    parts = urlsplit(href)
    return parts.scheme == "https" and (parts.hostname or "") in EXTERNAL_HOSTS and not parts.username


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextBlock(_Block):
    type: Literal["text"] = "text"
    text: str


class Chip(_Block):
    """A complete request: pressing it sends the capability and parameters at once."""
    label: str = Field(min_length=1, max_length=80)
    capability: str = Field(min_length=1, max_length=60)
    params: dict[str, str] = Field(default_factory=dict)


class ChipsBlock(_Block):
    type: Literal["chips"] = "chips"
    items: list[Chip]


class Link(_Block):
    label: str = Field(min_length=1, max_length=80)
    href: str

    def model_post_init(self, __context) -> None:
        if not safe_href(self.href):
            raise ValueError("Link target is not allowed")


class LinksBlock(_Block):
    type: Literal["links"] = "links"
    items: list[Link]


class TableBlock(_Block):
    type: Literal["table"] = "table"
    columns: list[str]
    rows: list[list[str]]

    def model_post_init(self, __context) -> None:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Table rows must match the columns")


class TaskBlock(_Block):
    type: Literal["task"] = "task"
    task_id: str
    title: str
    status: str
    href: str

    def model_post_init(self, __context) -> None:
        if not safe_href(self.href):
            raise ValueError("Link target is not allowed")


class MaterialBlock(_Block):
    """A ready material shown as a card. Its buttons are ordinary chips and links next to it."""
    type: Literal["material"] = "material"
    artifact_id: str
    kind: str = Field(max_length=40)
    title: str = Field(max_length=300)
    status: str = Field(max_length=40)
    excerpt: str = Field(default="", max_length=800)
    href: str

    def model_post_init(self, __context) -> None:
        if not safe_href(self.href):
            raise ValueError("Link target is not allowed")


class ConfirmBlock(_Block):
    """Asks before an action with consequences. Voice 'yes' answers only the latest open card."""
    type: Literal["confirm"] = "confirm"
    confirmation_id: str
    title: str
    details: list[str] = Field(default_factory=list)
    confirm_label: str = "Подтвердить"
    cancel_label: str = "Отмена"
    # Shown on the card so nobody confirms a payment they did not understand.
    paid: bool = False
    state: Literal["pending", "confirmed", "declined"] = "pending"


Block = Annotated[
    TextBlock | ChipsBlock | LinksBlock | TableBlock | TaskBlock | MaterialBlock | ConfirmBlock,
    Field(discriminator="type"),
]
_adapter: TypeAdapter[Block] = TypeAdapter(Block)


def clean_blocks(raw: list[dict]) -> list[dict]:
    """Keep only valid blocks, as plain dicts ready for JSON storage."""
    kept: list[dict] = []
    for item in raw:
        try:
            kept.append(_adapter.validate_python(item).model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError):
            continue
    return kept
