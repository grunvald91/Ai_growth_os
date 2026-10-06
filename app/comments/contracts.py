from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr

Platform = Literal["youtube", "telegram", "instagram", "facebook", "vk", "tiktok", "dzen", "vc_ru", "tenchat", "own_site"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(Strict):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)


class AgentSettings(Strict):
    tone: Literal["friendly", "professional", "warm"] = "friendly"
    max_length: int = Field(default=500, ge=100, le=1500)
    emoji: bool = False
    support_contact: str = Field(default="", max_length=500)
    next_step: str = Field(default="", max_length=500)
    facts: list[Fact] = Field(default_factory=list, max_length=100)
    examples: str = Field(default="", max_length=6000)
    forbidden_claims: str = Field(default="", max_length=3000)
    enabled: bool = True


class CommentInput(Strict):
    platform: Platform
    text: str = Field(min_length=1, max_length=8000)
    post_text: str = Field(default="", max_length=8000)


class ConnectInput(Strict):
    platform: Literal["youtube", "telegram", "instagram"]
    account_id: str = Field(min_length=1, max_length=200)
    token: SecretStr


class ReplyDraft(Strict):
    intent: Literal["question", "purchase_interest", "objection", "feedback", "complaint", "spam", "other"]
    action: Literal["draft", "human_review", "skip"]
    reply: str = Field(default="", max_length=1500)
    reason: str = Field(max_length=1500)
    used_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    missing_information: list[str] = Field(default_factory=list, max_length=20)


class ReplyEdit(Strict):
    reply: str = Field(min_length=1, max_length=1500)
