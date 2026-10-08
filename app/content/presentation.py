"""Reader-facing material extraction shared by the studio and Telegram.

Artifacts contain workflow metadata as well as copy. Only explicitly selected
editorial fields belong in previews or the clipboard; never stringify a payload.
"""
import json
import re
from html.parser import HTMLParser

from markdown_it import MarkdownIt

PLATFORM_LABELS = {
    'own_site': 'SEO / сайт', 'dzen': 'Дзен', 'tenchat': 'TenChat',
    'vc_ru': 'vc.ru', 'telegram': 'Telegram', 'faq': 'FAQ',
    'founder': 'Пост автора', 'youtube_shorts': 'YouTube Shorts',
    'instagram_reels': 'Instagram Reels', 'vk_clips': 'VK Клипы',
    'vk': 'VK', 'vk_video': 'VK Видео', 'instagram': 'Instagram',
    'youtube': 'YouTube', 'threads': 'Threads',
}


def payload(record):
    try:
        value = json.loads(record.content)
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def text(value):
    return value.strip() if isinstance(value, str) else ''


def section(label, value):
    if isinstance(value, list):
        value = '\n'.join('- ' + text(item) for item in value if text(item))
    value = text(value)
    return f'## {label}\n\n{value}' if value else ''


def material_platform(record, value=None):
    value = payload(record) if value is None else value
    brief = value.get('production_brief')
    brief = brief if isinstance(brief, dict) else value
    return (brief.get('target_platform') or value.get('channel')
            or {'article': 'own_site', 'dzen_brief': 'dzen',
                'telegram_brief': 'telegram'}.get(record.kind, ''))


def material_markdown(record, value=None):
    value = payload(record) if value is None else value
    if not value:
        # Legacy prose is useful; malformed/unknown structured data is not copy.
        raw = text(record.content)
        return raw if raw and not raw.startswith(('{', '[')) else ''
    if record.kind == 'article':
        for key in ('full_article', 'body'):
            if text(value.get(key)):
                return text(value[key])
        reader = value.get('reader_article')
        if isinstance(reader, str):
            return reader.strip()
        if isinstance(reader, dict):
            parts = ['# ' + text(reader.get('title') or record.title), text(reader.get('lead'))]
            facts = [text(item.get('text')) for item in reader.get('fact_renderings', [])
                     if isinstance(item, dict) and text(item.get('text'))]
            parts.append(section('Проверенные факты', facts))
            parts.extend(section(item.get('heading', ''), item.get('body'))
                         for item in reader.get('sections', []) if isinstance(item, dict))
            parts.append(text(reader.get('closing')))
            conversion = value.get('conversion')
            if isinstance(conversion, dict):
                parts.append(text(conversion.get('cta')))
            return '\n\n'.join(part for part in parts if part)
        return ''
    if record.kind == 'channel_text':
        body = text(value.get('body') or value.get('text'))
        if not body:
            return ''
        title = text(value.get('title'))
        # Some writers include the headline in their body already.
        first = body.splitlines()[0].lstrip('# ').strip()
        return f'# {title}\n\n{body}' if title and first != title else body
    if record.kind == 'content_distribution':
        channels = [PLATFORM_LABELS.get(key, key) for key in value.get('channels', [])
                    if isinstance(key, str)]
        parts = [section('Площадки', channels),
                 'После проверки разместите материал и сохраните ссылку на публикацию.']
        measurement = value.get('measurement')
        if isinstance(measurement, dict):
            for key, label in [('content', 'Просмотры'), ('traffic', 'Переходы'),
                               ('lead', 'Обращения'), ('activation', 'Активации'),
                               ('customer', 'Клиенты')]:
                stage = measurement.get(key)
                if isinstance(stage, dict):
                    parts.append(section(label, stage.get('attribution')))
            parts.append(section('Условие успеха', measurement.get('success_hypothesis')))
        return '\n\n'.join(part for part in parts if part)
    brief = value.get('production_brief')
    brief = brief if isinstance(brief, dict) else value
    if record.kind in {'video_script', 'short_video_brief', 'story_brief', 'static_image_brief'}:
        fields = [
            ('objective', 'Задача'), ('hook_variants', 'Варианты начала'),
            ('creative_concept', 'Идея'), ('narrative_arc', 'История'),
            ('visual_style', 'Визуальный стиль'), ('setting_and_props', 'Место и реквизит'),
            ('talent_direction', 'Рекомендации ведущему'),
            ('voiceover_or_dialogue_script', 'Текст для озвучки / диалога'),
            ('subject', 'Что в кадре'), ('composition', 'Композиция'),
            ('background_and_environment', 'Фон'), ('lighting_and_mood', 'Свет и настроение'),
            ('overlay_text', 'Текст на изображении'), ('brand_elements', 'Элементы бренда'),
            ('typography_direction', 'Оформление текста'), ('cta_treatment', 'Призыв к действию'),
        ]
        parts = [section(label, brief.get(key)) for key, label in fields]
        for index, shot in enumerate(brief.get('shots') or brief.get('frames') or [], 1):
            if not isinstance(shot, dict):
                continue
            timing = (f" · {shot['start_second']}–{shot['end_second']} сек."
                      if 'start_second' in shot and 'end_second' in shot else '')
            lines = []
            for key, label in [('purpose', 'Цель'), ('visual_description', 'Кадр'),
                               ('camera_and_movement', 'Камера'), ('composition', 'Композиция'),
                               ('action', 'Действие'), ('spoken_text', 'Реплика'),
                               ('on_screen_text', 'На экране'), ('transition', 'Переход'),
                               ('animation_or_transition', 'Анимация'),
                               ('sound_or_music_note', 'Звук'), ('interaction', 'Интерактив')]:
                if text(shot.get(key)):
                    lines.append(f'**{label}:** {text(shot[key])}')
            parts.append(section(f'Сцена {index}{timing}', '\n\n'.join(lines)))
        for key, label in [('subtitle_style', 'Субтитры'), ('music_direction', 'Музыка'),
                           ('editing_mix_notes', 'Монтаж и сведение'), ('editing_rhythm', 'Ритм монтажа'),
                           ('cover_frame', 'Обложка'), ('cta_frame', 'Финальный кадр'),
                           ('caption_variants', 'Варианты описания'), ('final_cta', 'Призыв к действию')]:
            parts.append(section(label, brief.get(key)))
        if not any(parts):
            return text(value.get('script'))
        return '\n\n'.join(part for part in parts if part)
    # Planning briefs are instructions, not complete social posts.
    for key in ('body', 'text', 'copy', 'description', 'content'):
        if text(value.get(key)):
            return text(value[key])
    if record.kind.endswith('_brief'):
        return '\n\n'.join(part for part in [
            section('Начало', value.get('hook')), section('План материала', value.get('outline')),
            section('Подача', value.get('framing')), section('Призыв к действию', value.get('cta')),
            section('Рекомендации', value.get('adaptation_notes')),
        ] if part)
    return ''


def formatted_html(markdown):
    renderer = MarkdownIt('commonmark', {'html': False, 'breaks': True})
    # Technical fenced blocks are not reader-facing publication copy.
    tokens = [token for token in renderer.parse(markdown) if token.type not in {'fence', 'code_block'}]
    return renderer.renderer.render(tokens, renderer.options, {})


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.lists = []

    def handle_starttag(self, tag, attrs):
        if tag in {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'}:
            if not (tag == 'p' and self.lists):
                self.parts.append('\n\n')
        elif tag in {'ul', 'ol'}:
            self.lists.append([tag, int(dict(attrs).get('start', '1'))])
        elif tag == 'li':
            marker = '• '
            if self.lists and self.lists[-1][0] == 'ol':
                marker = str(self.lists[-1][1]) + '. '
                self.lists[-1][1] += 1
            self.parts.append('\n' + marker)
        elif tag == 'br':
            self.parts.append('\n')
        elif tag == 'a':
            self.links.append((dict(attrs).get('href', ''), len(self.parts)))
        elif tag == 'img':
            self.parts.append(dict(attrs).get('alt', ''))

    def handle_endtag(self, tag):
        if tag in {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'ul', 'ol'}:
            self.parts.append('\n\n')
        if tag in {'ul', 'ol'} and self.lists:
            self.lists.pop()
        if tag == 'a' and self.links:
            href, start = self.links.pop()
            label = ''.join(self.parts[start:])
            if href and href != label:
                self.parts.append(f' ({href})')

    def handle_data(self, data):
        if data.strip() or (data and '\n' not in data):
            self.parts.append(data)


def plain_text(markdown):
    parser = _PlainText()
    parser.feed(formatted_html(markdown))
    # Preserve paragraph boundaries without Markdown markers or wrapper metadata.
    return re.sub(r'\n{3,}', '\n\n', ''.join(parser.parts)).strip()
