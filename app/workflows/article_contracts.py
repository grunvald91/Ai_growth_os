"""Typed contracts for grounded content production and human-review-ready creative briefs."""
from difflib import SequenceMatcher
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=16000)]
LongText = Annotated[str, Field(min_length=1, max_length=48000)]
ShortText = Annotated[str, Field(min_length=1, max_length=1200)]
Channel = Literal['own_site', 'dzen', 'tenchat', 'vc_ru', 'telegram']
DerivativeKind = Literal[
    'dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder',
    'short_video', 'story', 'static_image'
]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class FaqItem(Contract):
    question: ShortText
    answer: Annotated[str, Field(min_length=40, max_length=1500)]


class SeoBrief(Contract):
    """A plan item the article must serve: a real collected query, never an invented one."""
    plan_item_id: str = Field(pattern=r'^a\d{3,5}$')
    primary_query: ShortText
    related_queries: list[ShortText] = Field(default_factory=list)
    stage: Literal['awareness', 'consideration', 'decision']
    intent: Literal['informational', 'commercial', 'transactional', 'navigational']
    reader_problem: Text
    ai_questions: list[ShortText] = Field(min_length=1)
    next_step: ShortText


class ArticleProductionInput(Contract):
    product_id: UUID
    language: Literal['ru', 'en'] = 'ru'
    task_id: UUID | None = None
    decision_id: UUID | None = None
    experiment_id: UUID | None = None
    channel: Channel = 'own_site'
    topic: ShortText
    audience: ShortText | None = None
    problem: Text | None = None
    editorial_brief: Text | None = None
    request_publication: bool = False
    produce_ready_assets: bool = False
    seo_brief: SeoBrief | None = None


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


class ArticleBodySection(Contract):
    heading: ShortText
    body: Text
    kind: Literal['advice', 'hypothesis']


class ArticleBodyDraft(Contract):
    sections: list[ArticleBodySection] = Field(min_length=4, max_length=20)
    proof_needed_notes: list[Text] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def long_form(self):
        if sum(len(section.body) for section in self.sections) < 2000:
            raise ValueError('A full long-form article requires at least 2000 body characters')
        return self


class ArticleDraft(Contract):
    sections: list[ArticleSection] = Field(min_length=4, max_length=25)
    claims: list[GroundedClaim] = Field(default_factory=list, max_length=30)
    proof_needed_notes: list[Text] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def long_form(self):
        if sum(len(section.body) for section in self.sections) < 2000:
            raise ValueError('A full long-form article requires at least 2000 body characters')
        return self


class ReaderSection(Contract):
    heading: ShortText
    body: Text


class RenderedFact(Contract):
    source_id: ShortText
    text: Text


class HumanEditedArticle(Contract):
    """Reader-facing copy. Verified product facts are injected separately by application code."""
    language: Literal['ru', 'en']
    title: ShortText
    lead: Text
    fact_renderings: list[RenderedFact] = Field(default_factory=list, max_length=30)
    sections: list[ReaderSection] = Field(min_length=4, max_length=20)
    closing: Text
    editorial_notes: list[Text] = Field(default_factory=list, max_length=10)
    # Search and AI-assistant surface: a short self-contained answer and real questions with answers.
    direct_answer: Annotated[str, Field(min_length=40, max_length=1200)] | None = None
    faq: list[FaqItem] = Field(default_factory=list)
    meta_description: Annotated[str, Field(min_length=40, max_length=320)] | None = None

    @model_validator(mode='after')
    def readable_long_form(self):
        body = '\n'.join([self.lead, *(s.body for s in self.sections), self.closing])
        if len(body) < 2500:
            raise ValueError('Human-facing article must be a complete long-form read')
        forbidden = ('[HYPOTHESIS]', '[ADVICE]', 'proof_needed_notes', 'schema:', 'agent note')
        if any(token.casefold() in body.casefold() for token in forbidden):
            raise ValueError('Internal workflow labels must not leak into reader-facing copy')
        if self.language == 'ru':
            letters = [ch for ch in body if ch.isalpha()]
            cyrillic = [
                ch for ch in letters
                if ('а' <= ch.casefold() <= 'я') or ch.casefold() == 'ё'
            ]
            if letters and len(cyrillic) / len(letters) < 0.55:
                raise ValueError('Russian reader-facing article must be predominantly Russian')
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
    review_window_days: int | None = Field(default=None, ge=1, le=90, strict=True)
    success_hypothesis: Text


class VideoShot(Contract):
    number: int = Field(ge=1, le=30, strict=True)
    start_second: float = Field(ge=0, le=90)
    end_second: float = Field(gt=0, le=90)
    purpose: ShortText
    shot_type: ShortText
    visual_description: Text
    camera_and_movement: Text
    action: Text
    spoken_text: Text | None = None
    on_screen_text: ShortText | None = None
    transition: ShortText
    sound_or_music_note: Text
    generation_prompt: LongText
    avoid_in_generation: Text | None = None

    @model_validator(mode='after')
    def valid_timing(self):
        if self.end_second <= self.start_second:
            raise ValueError('Video shot end must be after start')
        return self


class ShortVideoProductionBrief(Contract):
    asset_type: Literal['short_video'] = 'short_video'
    target_platform: Literal['youtube_shorts', 'instagram_reels']
    aspect_ratio: Literal['9:16'] = '9:16'
    duration_seconds: int = Field(ge=15, le=90, strict=True)
    objective: Text
    hook_variants: list[ShortText] = Field(min_length=2, max_length=3)
    creative_concept: Text
    visual_style: Text
    setting_and_props: Text
    talent_direction: Text
    voiceover_or_dialogue_script: LongText
    shots: list[VideoShot] = Field(min_length=3, max_length=20)
    subtitle_style: Text
    music_direction: Text
    editing_rhythm: Text
    editing_mix_notes: Text
    cover_frame: Text
    cta_frame: Text
    caption_variants: list[Text] = Field(min_length=2, max_length=4)
    export_spec: Text
    production_notes: list[Text] = Field(min_length=1, max_length=15)


class StoryFrame(Contract):
    number: int = Field(ge=1, le=12, strict=True)
    duration_seconds: int = Field(ge=2, le=15, strict=True)
    purpose: ShortText
    visual_description: Text
    composition: Text
    on_screen_text: ShortText
    animation_or_transition: Text
    interaction: Text | None = None
    safe_area_note: Text


class StoryProductionBrief(Contract):
    asset_type: Literal['story'] = 'story'
    aspect_ratio: Literal['9:16'] = '9:16'
    objective: Text
    narrative_arc: Text
    visual_style: Text
    typography_direction: Text
    frames: list[StoryFrame] = Field(min_length=3, max_length=10)
    final_cta: Text
    export_spec: Text
    production_notes: list[Text] = Field(min_length=1, max_length=15)


class StaticImageProductionBrief(Contract):
    asset_type: Literal['static_image'] = 'static_image'
    target_formats: list[Literal['1:1', '4:5', '9:16']] = Field(min_length=1, max_length=3)
    objective: Text
    visual_style: Text
    subject: Text
    composition: Text
    background_and_environment: Text
    lighting_and_mood: Text
    typography_direction: Text
    overlay_text: ShortText
    brand_elements: Text
    image_generation_prompt: LongText
    avoid_in_visual: Text
    cta_treatment: Text
    export_spec: Text
    production_notes: list[Text] = Field(min_length=1, max_length=15)


CreativeProductionBrief = (
    ShortVideoProductionBrief | StoryProductionBrief | StaticImageProductionBrief
)


TextDerivativeKind = Literal['dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder']


class DerivativeCore(Contract):
    audience: ShortText
    framing: Text
    hook: Text
    outline: list[Text] = Field(min_length=2, max_length=10)
    cta: Text
    landing_page: LinkProposal
    adaptation_notes: Text


class TextDerivativeDraft(DerivativeCore):
    kind: TextDerivativeKind


class TextDerivativeSet(Contract):
    derivatives: list[TextDerivativeDraft] = Field(min_length=6, max_length=6)

    @model_validator(mode='after')
    def complete_text_set(self):
        expected = {'dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder'}
        if {item.kind for item in self.derivatives} != expected:
            raise ValueError('Provide exactly the six supported text derivatives')
        return self


class ShortVideoDerivativeDraft(DerivativeCore):
    kind: Literal['short_video'] = 'short_video'
    video_mode: Literal['brief_only'] = 'brief_only'
    production_brief: ShortVideoProductionBrief


class StoryDerivativeDraft(DerivativeCore):
    kind: Literal['story'] = 'story'
    production_brief: StoryProductionBrief


class StaticImageDerivativeDraft(DerivativeCore):
    kind: Literal['static_image'] = 'static_image'
    production_brief: StaticImageProductionBrief


class DerivativeDraft(Contract):
    kind: DerivativeKind
    audience: ShortText
    framing: Text
    hook: Text
    outline: list[Text] = Field(min_length=2, max_length=10)
    cta: Text
    landing_page: LinkProposal
    adaptation_notes: Text
    video_mode: Literal['brief_only'] | None = None
    production_brief: CreativeProductionBrief | None = None

    @model_validator(mode='after')
    def creative_brief_matches_kind(self):
        expected = {
            'short_video': ShortVideoProductionBrief,
            'story': StoryProductionBrief,
            'static_image': StaticImageProductionBrief,
        }
        expected_type = expected.get(self.kind)
        if expected_type is None:
            if self.production_brief is not None or self.video_mode is not None:
                raise ValueError('Non-visual derivatives must not contain production briefs')
            return self
        if not isinstance(self.production_brief, expected_type):
            raise ValueError(f'{self.kind} requires its full textual production brief')
        if self.kind == 'short_video':
            if self.video_mode != 'brief_only':
                raise ValueError('Short video is a text production brief only')
        elif self.video_mode is not None:
            raise ValueError('video_mode belongs only to short_video')
        return self


class DerivativeSet(Contract):
    derivatives: list[DerivativeDraft] = Field(min_length=9, max_length=9)

    @model_validator(mode='after')
    def distinct_derivatives(self):
        expected = {
            'dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder',
            'short_video', 'story', 'static_image'
        }
        if {brief.kind for brief in self.derivatives} != expected:
            raise ValueError('Provide exactly one brief per derivative kind')
        for field in ('framing', 'hook'):
            texts = [' '.join(getattr(brief, field).casefold().split()) for brief in self.derivatives]
            for i, value in enumerate(texts):
                if any(SequenceMatcher(None, value, other).ratio() >= .85 for other in texts[:i]):
                    raise ValueError('Derivative framing and hooks must be distinct, not blind copies')
        return self


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
    production_brief: CreativeProductionBrief | None = None

    @model_validator(mode='after')
    def creative_brief_matches_kind(self):
        expected = {
            'short_video': ShortVideoProductionBrief,
            'story': StoryProductionBrief,
            'static_image': StaticImageProductionBrief,
        }
        expected_type = expected.get(self.kind)
        if expected_type is None:
            if self.production_brief is not None or self.video_mode is not None:
                raise ValueError('Non-visual derivatives must not contain production briefs')
            return self
        if not isinstance(self.production_brief, expected_type):
            raise ValueError(f'{self.kind} requires its full textual production brief')
        if self.kind == 'short_video':
            if self.video_mode != 'brief_only':
                raise ValueError('Short video is a text production brief only')
        elif self.video_mode is not None:
            raise ValueError('video_mode belongs only to short_video')
        return self


class DistributionStep(Contract):
    channel: Channel
    action: Literal['prepare_draft', 'request_review', 'request_publication']
    rationale: Text
    downstream_goal: Literal['lead', 'activation', 'customer']


class ConversionCore(Contract):
    cta: Text
    landing_page: LinkProposal
    internal_links: list[LinkProposal] = Field(min_length=1, max_length=10)
    measurement: MeasurementPlan
    distribution: list[DistributionStep] = Field(min_length=1, max_length=10)


class ConversionPackage(ConversionCore):
    derivatives: list[DerivativeBrief] = Field(min_length=9, max_length=9)

    @model_validator(mode='after')
    def distinct_derivatives(self):
        expected = {
            'dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder',
            'short_video', 'story', 'static_image'
        }
        if {brief.kind for brief in self.derivatives} != expected:
            raise ValueError('Provide exactly one brief per derivative kind')
        return self


class ArticleArtifact(Contract):
    schema_version: Literal['2A1', '2A2'] = '2A2'
    product_id: UUID
    task_id: UUID
    decision_id: UUID | None
    experiment_id: UUID | None
    channel: Channel
    topic: ShortText
    strategy: ArticleStrategy
    seo: SEOPlan
    draft: ArticleDraft
    reader_article: HumanEditedArticle | None = None
    seo_brief: SeoBrief | None = None
    full_article: str = Field(min_length=2000, max_length=110000)
    conversion: ConversionPackage
    grounding_policy: Literal[
        'verbatim_product_facts_and_labeled_advice',
        'source_traced_human_edit',
    ] = 'source_traced_human_edit'

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
    checkpoint_artifact_ids: list[UUID] = Field(default_factory=list)
    approval_ids: list[UUID] = Field(default_factory=list)
    status: Literal['completed', 'failed']
    error: str | None = None
