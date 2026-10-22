"""Deterministic filtering and the restricted Telegram editor format."""
import re
import unicodedata
from telethon.extensions import html as telegram_html
from app.message_copy import SafeHtml

DEFAULT_AD_TERMS = '广告合作\n广告招商\n广告位招租\n付费推广\n商务合作\n推广合作'


def terms(value):
    return list(dict.fromkeys(x.strip() for x in re.split(r'[\n,，]+', value) if x.strip()))


def normalized(text):
    return unicodedata.normalize('NFKC', text).casefold()


def filter_post(text, exclude_terms, filter_ads, ad_terms):
    content = normalized(text)
    for term in terms(exclude_terms):
        if normalized(term) in content:
            return '命中排除词：' + term
    if filter_ads:
        for term in terms(ad_terms):
            if normalized(term) in content:
                return '广告规则：' + term
        if re.search(r'(?im)^\s*(?:\[ad\]|【广告】|\[广告\]|广告[:：])', text):
            return '广告规则：广告标记'
    return None


def prepare_post(markup, with_media=False):
    parser = SafeHtml()
    parser.feed(markup)
    parser.close()
    if parser.stack:
        raise ValueError('文字格式没有正确闭合')
    clean = ''.join(parser.parts)
    text, entities = telegram_html.parse(clean)
    limit = 1024 if with_media else 4096
    if len(text.encode('utf-16-le')) // 2 > limit:
        raise ValueError(f'正文过长，当前格式上限为 {limit} 字符；请精简正文或移除原附件')
    if not text.strip() and not with_media:
        raise ValueError('请填写正文或保留原附件')
    return text, entities, clean
