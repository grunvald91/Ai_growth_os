"""Publication-ready derivatives, distinct from the existing planning briefs."""
from difflib import SequenceMatcher
from typing import Literal
from pydantic import Field, model_validator
from app.workflows.article_contracts import Contract, ShortText, LongText, ShortVideoDerivativeDraft

class ReadyText(Contract):
    channel: Literal['dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder']
    title: ShortText
    body: LongText = Field(min_length=500)
    editorial_checks: list[ShortText] = Field(min_length=1, max_length=10)

class ReadyTextSet(Contract):
    texts: list[ReadyText] = Field(min_length=6, max_length=6)

    @model_validator(mode='after')
    def distinct(self):
        if {t.channel for t in self.texts} != {'dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder'}:
            raise ValueError('One complete text per supported channel required')
        bodies = [' '.join(t.body.casefold().split()) for t in self.texts]
        for i, body in enumerate(bodies):
            if any(SequenceMatcher(None, body, other).ratio() >= .85 for other in bodies[:i]):
                raise ValueError('Channel texts must be independently adapted')
        for t in self.texts:
            if t.channel in {'dzen', 'vc_ru'} and len(t.body) < 2000:
                raise ValueError('Dzen and vc.ru require full articles')
        return self

class VideoVariants(Contract):
    videos: list[ShortVideoDerivativeDraft] = Field(min_length=3, max_length=3)

    @model_validator(mode='after')
    def thirty_seconds(self):
        for v in self.videos:
            b = v.production_brief
            if b.duration_seconds != 30 or b.shots[0].start_second != 0 or b.shots[-1].end_second != 30:
                raise ValueError('Videos must cover exactly 30 seconds')
            if any(a.end_second != z.start_second for a, z in zip(b.shots, b.shots[1:])):
                raise ValueError('Shot timeline must be contiguous')
            if not all(s.on_screen_text for s in b.shots):
                raise ValueError('Every rendered scene needs visible copy')
        if len({v.hook.casefold() for v in self.videos}) != 3:
            raise ValueError('Three distinct video hooks required')
        return self
