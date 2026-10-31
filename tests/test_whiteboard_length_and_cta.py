"""One-minute videos (six scenes) and the closing call to action. No live provider calls."""
import json
import subprocess
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.api import whiteboard as api
from app.content.whiteboard.adapters import (
    LLMStoryboardProvider, closing_beat, storyboard_schema, with_call_to_action,
)
from app.content.whiteboard.contracts import (
    DEFAULT_CALL_TO_ACTION, WhiteboardBeat, WhiteboardRegion, WhiteboardRegionPlan,
    WhiteboardRequest, WhiteboardScene, WhiteboardStoryboard,
)
from app.content.whiteboard.jobs import JobRequest, owner_task, records, required_calls
from app.content.whiteboard.render import MarkerSweepRenderer
from app.content.whiteboard.review import VisualIssue, VisualReview
from app.content.whiteboard.runtime import configured_models
from app.content.whiteboard.worker import run_whiteboard_once
from app.llm.schemas import LLMResponse
from app.llm.service import LLMService
from app.models.domain import Task, TaskStatus
from app.privacy import PrivacyGateway

from test_whiteboard import PNG, FakeImages
from test_whiteboard_jobs import approve, client, environment, ready  # noqa: F401  (fixtures)


def long_request(**changes):
    values = {'product_id': uuid4(), 'topic': 'Зачем бизнесу онлайн-запись', 'duration_seconds': 60, 'scenes': 6}
    values.update(changes)
    return WhiteboardRequest(**values)


def two_beat_scene(index=0):
    return WhiteboardScene(title=f'Сцена номер {index + 1}', beats=[
        WhiteboardBeat(label='Первое', narration=f'Первая реплика сцены {index + 1}.', visual='Нарисованный календарь'),
        WhiteboardBeat(label='Второе', narration=f'Вторая реплика сцены {index + 1}.', visual='Нарисованный телефон'),
    ])


def story(scenes):
    return WhiteboardStoryboard(scenes=[two_beat_scene(i) for i in range(scenes)])


# --- the request -----------------------------------------------------------

def test_length_and_scene_count_go_together():
    assert long_request().scenes == 6
    with pytest.raises(ValidationError):
        WhiteboardRequest(product_id=uuid4(), topic='Зачем бизнесу онлайн-запись', duration_seconds=60, scenes=3)
    with pytest.raises(ValidationError):
        WhiteboardRequest(product_id=uuid4(), topic='Зачем бизнесу онлайн-запись', duration_seconds=30, scenes=6)


def test_empty_call_to_action_keeps_the_old_serialization():
    plain = WhiteboardRequest(product_id=uuid4(), topic='Зачем бизнесу онлайн-запись')
    assert 'call_to_action' not in plain.model_dump(mode='json')
    assert 'call_to_action' not in plain.model_dump_json()
    custom = plain.model_copy(update={'call_to_action': 'Пишите в комментариях, что объяснить дальше'})
    assert WhiteboardRequest.model_validate_json(custom.model_dump_json()) == custom
    assert custom.model_dump(mode='json')['call_to_action'].startswith('Пишите')
    # A request stored before the field existed still loads.
    assert WhiteboardRequest.model_validate(plain.model_dump(mode='json')).call_to_action == ''


def test_required_calls_match_what_a_video_costs():
    short, long = long_request(duration_seconds=30, scenes=3), long_request()
    assert required_calls(short) == 14 and required_calls(short, visual_review=True) == 19
    assert required_calls(long) == 26 and required_calls(long, visual_review=True) == 34


def test_a_minute_video_cannot_be_approved_for_too_few_calls():
    from app.content.whiteboard.runtime import WhiteboardModels
    models = WhiteboardModels(storyboard='kimi-2.7', image='google/image', speech='google/tts', regions='google/vision')
    with pytest.raises(ValidationError):
        JobRequest(request=long_request(), models=models, max_api_calls=25)
    assert JobRequest(request=long_request(), models=models, max_api_calls=26).max_api_calls == 26


# --- the closing call to action -------------------------------------------

def test_default_closing_beat_asks_to_subscribe_and_like():
    beat = closing_beat(long_request())
    assert beat.narration == DEFAULT_CALL_TO_ACTION
    assert 'Подпишитесь' in beat.narration and 'лайк' in beat.narration


def test_owner_call_to_action_is_spoken_as_written():
    text = 'Напишите в комментариях, что объяснить дальше'
    assert closing_beat(long_request(call_to_action=text)).narration == text


def test_call_to_action_is_added_to_the_last_scene_only():
    result = with_call_to_action(story(3), long_request(duration_seconds=30, scenes=3))
    assert [len(s.beats) for s in result.scenes] == [2, 2, 3]
    assert result.scenes[-1].beats[-1].narration == DEFAULT_CALL_TO_ACTION
    assert result.scenes[-1].beats[:2] == story(3).scenes[-1].beats


def test_call_to_action_replaces_the_last_beat_of_a_full_scene():
    full = WhiteboardScene(title='Полная сцена', beats=[WhiteboardBeat(
        label=f'Бит {i}', narration=f'Реплика номер {i}.', visual='Нарисованный значок') for i in range(6)])
    result = with_call_to_action(WhiteboardStoryboard(scenes=[two_beat_scene(0), two_beat_scene(1), full]),
                                 long_request(duration_seconds=30, scenes=3))
    assert len(result.scenes[-1].beats) == 6
    assert result.scenes[-1].beats[-1].narration == DEFAULT_CALL_TO_ACTION


@pytest.mark.asyncio
@pytest.mark.parametrize('scenes,seconds,words', [(3, 30, 'three scenes'), (6, 60, 'six scenes')])
async def test_storyboard_request_matches_the_chosen_length(scenes, seconds, words):
    seen = {}

    class Provider:
        async def generate(self, req):
            seen['request'] = req
            return LLMResponse(model='test-model', content=story(scenes).model_dump_json())

    service = LLMService(privacy_gateway=PrivacyGateway(), provider=Provider())
    request = long_request(duration_seconds=seconds, scenes=scenes)
    got = await LLMStoryboardProvider(service, model='test-model').plan(request)
    asked = seen['request']
    assert words in asked.system_prompt and 'closing call is added separately' in asked.system_prompt
    assert asked.response_schema['properties']['scenes']['minItems'] == scenes
    assert asked.response_schema['properties']['scenes']['maxItems'] == scenes
    assert len(got.scenes) == scenes and len(got.scenes[-1].beats) == 3
    assert got.scenes[-1].beats[-1].narration == DEFAULT_CALL_TO_ACTION


def test_storyboard_schema_has_exact_scene_count():
    assert storyboard_schema(6)['properties']['scenes']['minItems'] == 6
    assert WhiteboardStoryboard.model_json_schema()['properties']['scenes']['maxItems'] == 6


# --- picture review with six scenes ---------------------------------------

def issue(scene, **changes):
    values = dict(id=f's{scene}-hook', scene_index=scene, beat_index=0, severity='major', confidence='high',
                  category='mechanics', evidence='Крюк нарисован не с той стороны.',
                  correction='Нарисовать крюк с задней стороны самолёта.')
    values.update(changes)
    return VisualIssue(**values)


def test_review_covers_every_scene_of_a_six_scene_video():
    review = VisualReview(checked_scenes=list(range(6)), issues=[issue(5)])
    review.check_for(story(6))
    with pytest.raises(ValueError):
        review.check_for(story(3))
    with pytest.raises(ValueError):
        VisualReview(checked_scenes=[0, 1, 2, 3, 4, 4])
    with pytest.raises(ValueError):
        VisualReview(checked_scenes=[0, 1, 2]).check_for(story(6))


# --- the real renderer -----------------------------------------------------

def test_renderer_makes_a_minute_video_with_a_three_beat_last_scene(tmp_path):
    audio = tmp_path / 'tone.mp3'
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                    '-codec:a', 'libmp3lame', str(audio)], check=True)
    from PIL import Image
    import io
    image = io.BytesIO(); Image.new('RGB', (180, 320), 'white').save(image, format='PNG')
    from app.content.whiteboard.contracts import WhiteboardSceneAssets
    scenes = []
    for index, scene in enumerate(with_call_to_action(story(6), long_request()).scenes):
        count = len(scene.beats)
        regions = WhiteboardRegionPlan(regions=[
            WhiteboardRegion(beat_index=i, box=(0, i * 1000 // count, 1000, (i + 1) * 1000 // count)) for i in range(count)])
        scenes.append(WhiteboardSceneAssets(scene, image.getvalue(), tuple(audio.read_bytes() for _ in range(count)), regions))
    out = tmp_path / 'out'; out.mkdir()
    seconds = MarkerSweepRenderer(width=180, height=320, fps=8).render_sync(tuple(scenes), out / 'video.mp4', long_request())
    assert seconds == 60.0 and (out / 'video.mp4').stat().st_size > 0
    assert len(json.loads((out / 'video.json').read_text())['beats']) == 13


def test_narration_beyond_a_minute_still_fits_the_long_video(tmp_path):
    from PIL import Image
    import io
    audio = tmp_path / 'long.mp3'
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=5',
                    '-codec:a', 'libmp3lame', str(audio)], check=True)
    image = io.BytesIO(); Image.new('RGB', (180, 320), 'white').save(image, format='PNG')
    from app.content.whiteboard.contracts import WhiteboardSceneAssets
    scenes = []
    for scene in story(6).scenes:  # 12 beats x 5 s = 60 s of speech, plus a tail: over the old 60-second cap
        regions = WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=0, box=(0, 0, 1000, 500)),
                                                WhiteboardRegion(beat_index=1, box=(0, 500, 1000, 1000))])
        scenes.append(WhiteboardSceneAssets(scene, image.getvalue(), (audio.read_bytes(), audio.read_bytes()), regions))
    out = tmp_path / 'out'; out.mkdir()
    seconds = MarkerSweepRenderer(width=180, height=320, fps=5).render_sync(tuple(scenes), out / 'video.mp4', long_request())
    assert 61 <= seconds <= 120


# --- owner API and form -----------------------------------------------------

def create(browser, product, **body):
    return browser.post(f'/products/{product}/whiteboard/jobs', json={'topic': 'Почему самолёт садится на авианосец', **body})


def test_api_defaults_to_thirty_seconds_with_exactly_the_needed_calls(environment):
    _, _, product, _ = environment
    with client() as browser:
        state = create(browser, product).json()
        assert state['request']['scenes'] == 3 and state['request']['duration_seconds'] == 30
        assert state['max_api_calls'] == 14
        assert create(browser, product, visual_review=True).json()['max_api_calls'] == 19


def test_api_minute_video_has_six_scenes_and_a_larger_call_limit(environment):
    _, _, product, _ = environment
    with client() as browser:
        state = create(browser, product, duration_seconds=60, call_to_action='Напишите, что объяснить дальше').json()
        assert state['request']['scenes'] == 6 and state['request']['duration_seconds'] == 60
        assert state['request']['call_to_action'] == 'Напишите, что объяснить дальше'
        assert state['max_api_calls'] == 26
        assert create(browser, product, duration_seconds=60, visual_review=True).json()['max_api_calls'] == 34


def test_api_rejects_a_limit_below_what_the_video_needs(environment):
    factory, _, product, _ = environment
    with client() as browser:
        low = create(browser, product, duration_seconds=60, max_api_calls=25)
        assert low.status_code == 422 and '26' in low.json()['detail']
        assert create(browser, product, max_api_calls=13).status_code == 422
        assert create(browser, product, call_to_action='Да').status_code == 422
        assert create(browser, product, duration_seconds=45).status_code == 422
    with factory() as db:
        assert list(db.scalars(select(Task))) == []


def test_call_to_action_with_a_secret_is_refused(environment):
    factory, _, product, _ = environment
    with client() as browser:
        refused = create(browser, product, call_to_action='Подпишитесь, мой api_key: sk-ant-abcdefgh12345678')
        assert refused.status_code == 422
    with factory() as db:
        assert list(db.scalars(select(Task))) == []


def test_form_offers_length_and_call_to_action(environment):
    _, _, product, _ = environment
    with client() as browser:
        page = browser.get(f'/studio/products/{product}/whiteboard').text
    assert 'name="duration_seconds"' in page and 'Около минуты' in page
    assert 'name="call_to_action"' in page and 'подписаться на канал и поставить лайк' in page
    assert 'value="auto" selected' in page


def test_form_creates_a_minute_job_and_the_page_says_so(environment):
    factory, _, product, _ = environment
    with client() as browser:
        response = browser.post(f'/studio/products/{product}/whiteboard', follow_redirects=False, data={
            'topic': 'Как работает онлайн-запись', 'duration_seconds': '60',
            'call_to_action': 'Напишите, что объяснить дальше', 'max_api_calls': 'auto', 'visual_review': 'off'})
        assert response.status_code == 303
        page = browser.get(response.headers['location']).text
        assert 'Около минуты' in page and '6 сцен' in page and 'Напишите, что объяснить дальше' in page
        assert 'до 26 API-вызовов' in page
        too_few = browser.post(f'/studio/products/{product}/whiteboard', data={
            'topic': 'Как работает онлайн-запись', 'duration_seconds': '60', 'max_api_calls': '19', 'visual_review': 'off'})
        assert too_few.status_code == 422 and 'не меньше 26' in too_few.text


# --- the worker: the promised number of calls is the number actually made ---

@pytest.mark.asyncio
@pytest.mark.parametrize('seconds,scenes,limit', [(30, 3, 14), (60, 6, 26)])
async def test_worker_finishes_inside_the_calculated_limit(environment, seconds, scenes, limit):
    factory, settings, product, _ = environment
    with client() as browser:
        task_id = UUID(create(browser, product, duration_seconds=seconds).json()['task_id'])
    approve(environment, task_id)
    heard = []

    def build(job_settings, payload, store):
        from app.content.whiteboard.pipeline import WhiteboardPipeline

        class Planner:
            async def plan(self, request):
                return with_call_to_action(story(request.scenes), request)

        class Speech:
            async def synthesize(self, text, *, language, voice):
                heard.append(text)
                return b'ID3' + text.encode()

        class Regions:
            async def detect(self, scene, image):
                count = len(scene.beats)
                return WhiteboardRegionPlan(regions=[WhiteboardRegion(
                    beat_index=i, box=(0, i * 1000 // count, 1000, (i + 1) * 1000 // count)) for i in range(count)])

        class Renderer:
            async def render(self, scenes, destination, request):
                assert len(scenes) == request.scenes
                destination.write_bytes(b'video')
                return float(request.duration_seconds)

        return WhiteboardPipeline(storyboard=Planner(), images=FakeImages(), speech=Speech(), regions=Regions(),
                                  renderer=Renderer(), sanitizer=PrivacyGateway(), checkpoints=store,
                                  max_api_calls=payload.max_api_calls)

    assert await run_whiteboard_once(factory, settings, factory=build, readiness=ready) == 1
    with factory() as db:
        task = owner_task(db, product, task_id)
        _, saved, _, payload = records(db, task)
        state = json.loads(saved.content)
        assert task.status == TaskStatus.COMPLETED, state
        assert state['calls'] == limit == payload.max_api_calls
    assert heard[-1] == DEFAULT_CALL_TO_ACTION and len(heard) == 2 * scenes + 1


# --- review fixes ------------------------------------------------------------

def three_beat_scene():
    return WhiteboardScene(title='Итоговая сцена', beats=[WhiteboardBeat(
        label=f'Бит {i}', narration=f'Реплика номер {i}.', visual='Нарисованный значок') for i in range(3)])


def banded_png(gaps=(330, 660)):
    from PIL import Image, ImageDraw
    import io
    image = Image.new('RGB', (180, 320), 'white'); draw = ImageDraw.Draw(image)
    edges = [0, *[g * 320 // 1000 for g in gaps], 320]
    for top, bottom in zip(edges, edges[1:]):
        draw.rectangle((20, top + 12, 160, bottom - 12), outline='black', width=3)
    output = io.BytesIO(); image.save(output, format='PNG'); return output.getvalue()


def test_overlapping_regions_in_the_call_to_action_scene_are_repaired_offline():
    from app.content.whiteboard.contracts import WhiteboardSceneAssets
    from app.content.whiteboard.layout import complete_diagram_regions
    from app.content.whiteboard.render import _validate_regions
    scene, image = three_beat_scene(), banded_png()
    plan = WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=0, box=(0, 0, 1000, 500)),
                                         WhiteboardRegion(beat_index=1, box=(0, 400, 1000, 800)),
                                         WhiteboardRegion(beat_index=2, box=(0, 700, 1000, 1000))])
    fixed, repair = complete_diagram_regions(scene, image, plan)
    assert repair['reason'] == 'overlap' and repair['provider_calls'] == 0 and repair['human_review_required']
    _validate_regions(WhiteboardSceneAssets(scene, image, (b'ID3-audio',) * 3, fixed))
    boxes = [r.box for r in sorted(fixed.regions, key=lambda r: r.beat_index)]
    assert boxes[0][1] == 0 and boxes[-1][3] == 1000
    assert all(a[3] == b[1] for a, b in zip(boxes, boxes[1:]))


def test_band_cuts_move_to_blank_rows_between_drawings():
    from app.content.whiteboard.layout import complete_diagram_regions
    plan = WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=i, box=(0, 0, 1000, 1000)) for i in range(3)])
    fixed, repair = complete_diagram_regions(three_beat_scene(), banded_png(gaps=(400, 700)), plan)
    cuts = [r.box[1] for r in sorted(fixed.regions, key=lambda r: r.beat_index)][1:]
    assert repair['method'] == 'blank_rows'
    assert abs(cuts[0] - 400) <= 40 and abs(cuts[1] - 700) <= 40


def test_valid_three_beat_plan_is_kept_unchanged():
    from app.content.whiteboard.layout import complete_diagram_regions
    plan = WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=i, box=(0, i * 333, 1000, (i + 1) * 333))
                                         for i in range(3)])
    assert complete_diagram_regions(three_beat_scene(), banded_png(), plan) == (plan, None)


def test_too_short_call_to_action_is_refused_before_any_paid_call():
    with pytest.raises(ValidationError):
        long_request(call_to_action='Да')
