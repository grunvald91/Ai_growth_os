"""Bounded contracts for the four-stage, draft-only article production pipeline."""
from difflib import SequenceMatcher
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=4000)]
ShortText = Annotated[str, Field(min_length=1, max_length=300)]
Channel = Literal['own_site', 'dzen', 'tenchat', 'vc_ru', 'telegram']
DerivativeKind = Literal['dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder', 'short_video']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ArticleProductionInput(Contract):
    product_id: UUID
    task_id: UUID | None = None
    decision_id: UUID | None = None
    experiment_id: UUID | None = None
    channel: Channel = 'own_site'
    topic: ShortText
    audience: ShortText | None = None
    problem: Text | None = None
    request_publication: bool = False


class ArticleStrategy(Contract):
    audience: ShortText
    pain: Text
    search_intent: Text
    angle: Text
    titles: list[ShortText] = Field(min_length=2, max_length=5)
    hypothesis: Text


class SEOPlan(Contract):
    primary_query: ShortText
    related_queries: list[ShortText] = Field(min_length=1, max_length=10)
    title: ShortText
    hook: Text
    outline: list[Text] = Field(min_length=3, max_length=15)
    adaptation_notes: Text


class GroundedClaim(Contract):
    """Only verbatim, explicitly factual product-context lines qualify as evidence."""
    claim: Text
    source_id: ShortText
    quote: Text

    @model_validator(mode='after')
    def exact_claim(self):
        if self.claim != self.quote:
            raise ValueError('Factual claims must preserve the source wording')
        return self


class ArticleSection(Contract):
    heading: ShortText
    body: Text
    kind: Literal['advice', 'hypothesis', 'fact']
    claim_ids: list[ShortText] = Field(default_factory=list, max_length=10)


class ArticleDraft(Contract):
    sections: list[ArticleSection] = Field(min_length=4, max_length=25)
    claims: list[GroundedClaim] = Field(default_factory=list, max_length=30)
    proof_needed_notes: list[Text] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def long_form(self):
        if sum(len(section.body) for section in self.sections) < 2000:
            raise ValueError('A full long-form article requires at least 2000 body characters')
        return self


class LinkProposal(Contract):
    label: ShortText
    destination: ShortText
    status: Literal['proposed_needs_verification'] = 'proposed_needs_verification'

    @model_validator(mode='after')
    def local_destination(self):
        if not self.destination.startswith('/') or self.destination.startswith('//'):
            raise ValueError('Use proposed local paths, not unverified external URLs')
        if any(c in self.destination for c in ('?', '#', '@', '\\', '\n', '\r')):
            raise ValueError('Destination must not contain credentials, queries or fragments')
        return self


class MeasurementStage(Contract):
    event: ShortText
    metric: ShortText
    attribution: Text


class MeasurementPlan(Contract):
    content: MeasurementStage
    traffic: MeasurementStage
    lead: MeasurementStage
    activation: MeasurementStage
    customer: MeasurementStage
    activation_definition: Literal['demo', 'registration', 'booking']
    review_window_days: int = Field(ge=1, le=90, strict=True)
    success_hypothesis: Text


class DerivativeBrief(Contract):
    kind: DerivativeKind
    audience: ShortText
    framing: Text
    hook: Text
    outline: list[Text] = Field(min_length=2, max_length=10)
    cta: Text
    landing_page: LinkProposal
    measurement: MeasurementPlan
    adaptation_notes: Text
    video_mode: Literal['brief_only'] | None = None

    @model_validator(mode='after')
    def video_is_brief(self):
        if (self.kind == 'short_video') != (self.video_mode == 'brief_only'):
            raise ValueError('Short video is a brief only, never a rendering request')
        return self


class DistributionStep(Contract):
    channel: Channel
    action: Literal['prepare_draft', 'request_review', 'request_publication']
    rationale: Text
    downstream_goal: Literal['lead', 'activation', 'customer']


class ConversionPackage(Contract):
    cta: Text
    landing_page: LinkProposal
    internal_links: list[LinkProposal] = Field(min_length=1, max_length=10)
    measurement: MeasurementPlan
    distribution: list[DistributionStep] = Field(min_length=1, max_length=10)
    derivatives: list[DerivativeBrief] = Field(min_length=7, max_length=7)

    @model_validator(mode='after')
    def distinct_derivatives(self):
        expected = {'dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder', 'short_video'}
        if {brief.kind for brief in self.derivatives} != expected:
            raise ValueError('Provide exactly one brief per derivative kind')
        for field in ('framing', 'hook'):
            texts = [' '.join(getattr(brief, field).casefold().split()) for brief in self.derivatives]
            for i, text in enumerate(texts):
                if any(SequenceMatcher(None, text, other).ratio() >= .85 for other in texts[:i]):
                    raise ValueError('Derivative framing and hooks must be distinct, not blind copies')
        return self


class ArticleArtifact(Contract):
    schema_version: Literal['2A1'] = '2A1'
    product_id: UUID
    task_id: UUID
    decision_id: UUID | None
    experiment_id: UUID | None
    channel: Channel
    topic: ShortText
    strategy: ArticleStrategy
    seo: SEOPlan
    draft: ArticleDraft
    full_article: str = Field(min_length=2000, max_length=110000)
    conversion: ConversionPackage
    grounding_policy: Literal['verbatim_product_facts_and_labeled_advice'] = 'verbatim_product_facts_and_labeled_advice'

    @model_validator(mode='after')
    def derivatives_adapt_source(self):
        normalize = lambda value: ' '.join(value.casefold().split())
        for brief in self.conversion.derivatives:
            for derivative, source in (
                (brief.hook, self.seo.hook),
                (brief.framing, self.strategy.angle),
                (' '.join(brief.outline), ' '.join(self.seo.outline)),
            ):
                if SequenceMatcher(None, normalize(derivative), normalize(source)).ratio() >= .85:
                    raise ValueError('Derivative hook, framing and outline must adapt the source article')
        return self


class ArticleProductionResult(Contract):
    product_id: UUID
    task_id: UUID
    run_ids: list[UUID] = Field(default_factory=list)
    artifact_ids: list[UUID] = Field(default_factory=list)
    approval_ids: list[UUID] = Field(default_factory=list)
    status: Literal['completed', 'failed']
    error: str | None = None
