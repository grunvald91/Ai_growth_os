"""Official direct-chat links; opening a link is never recorded as delivery."""
import re
from urllib.parse import quote, urlsplit

def chat_link(channel: str, target: str, body: str):
    if len(body) > 4000:
        raise ValueError('Draft is too long for a chat link')
    if channel == 'telegram':
        value = target.strip()
        if value.startswith(('https://t.me/', 'http://t.me/')):
            parsed = urlsplit(value)
            if parsed.query or parsed.fragment:
                raise ValueError('Use an exact public Telegram username')
            value = parsed.path.strip('/')
        value = value.removeprefix('@')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', value):
            raise ValueError('Invalid public Telegram contact')
        return 'https://t.me/' + value + '?text=' + quote(body, safe='')
    if channel == 'whatsapp':
        value = target.strip()
        if value.startswith('https://wa.me/'):
            value = urlsplit(value).path.strip('/')
        if not re.fullmatch(r'\+?[0-9 ()-]+', value):
            raise ValueError('Invalid WhatsApp phone')
        phone = re.sub(r'[^0-9]', '', value)
        if not re.fullmatch(r'[1-9][0-9]{7,14}', phone):
            raise ValueError('International phone required')
        return 'https://wa.me/' + phone + '?text=' + quote(body, safe='')
    raise ValueError('No direct link for this channel')
