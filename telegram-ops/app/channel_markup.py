"""Channel-only HTML validation and Telegram entities, including spoilers."""
from html.parser import HTMLParser
from telethon.helpers import add_surrogate, del_surrogate, strip_text
from telethon.tl import types
from app.message_copy import SafeHtml


class ChannelHtml(SafeHtml):
    allowed = SafeHtml.allowed | {'blockquote', 'tg-spoiler'}

    def handle_starttag(self, tag, attrs):
        tag = self.aliases.get(tag, tag)
        if (tag == 'code' and self.stack) or ('code' in self.stack and tag != 'br'):
            raise ValueError('等宽文字请单独使用，先清除该段其他格式再设置')
        if tag == 'blockquote' and 'blockquote' in self.stack:
            raise ValueError('引用中请勿再次嵌套引用')
        super().handle_starttag(tag, attrs)


class EntityParser(HTMLParser):
    """Track a stack instead of tag names, preserving nested tags and UTF-16 offsets."""
    kinds = {
        'b': types.MessageEntityBold, 'i': types.MessageEntityItalic,
        'u': types.MessageEntityUnderline, 'del': types.MessageEntityStrike,
        'code': types.MessageEntityCode, 'blockquote': types.MessageEntityBlockquote,
        'tg-spoiler': types.MessageEntitySpoiler, 'a': types.MessageEntityTextUrl,
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.entities, self.stack, self.utf16_offset = '', [], [], 0

    def handle_starttag(self, tag, attrs):
        args = {'url': dict(attrs)['href']} if tag == 'a' else {}
        self.stack.append(self.kinds[tag](offset=self.utf16_offset, length=0, **args))

    def handle_data(self, data):
        self.text += data
        self.utf16_offset += len(data.encode('utf-16-le')) // 2

    def handle_endtag(self, tag):
        entity = self.stack.pop()
        entity.length = self.utf16_offset - entity.offset
        if entity.length:
            self.entities.append(entity)


def parse_channel_html(markup):
    safe = ChannelHtml()
    safe.feed(markup)
    safe.close()
    if safe.stack:
        raise ValueError('文字格式没有正确闭合')
    clean = ''.join(safe.parts)
    parser = EntityParser()
    parser.feed(clean)
    parser.close()
    text = del_surrogate(strip_text(add_surrogate(parser.text), parser.entities))
    # Repeated nested formats are harmless; send one entity for identical spans.
    entities = list({(type(e).__name__, e.offset, e.length, getattr(e, 'url', None)): e for e in parser.entities}.values())
    entities.sort(key=lambda e: (e.offset, -e.length))
    return text, entities, clean
