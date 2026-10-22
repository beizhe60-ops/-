"""Deterministic source-based layout. No inferred amounts, identities or AI rewrites."""
import copy
import html
import re
from datetime import timezone
from zoneinfo import ZoneInfo
from typing import Literal

EmojiStyle = Literal["alert", "lively", "minimal"]
BodyFormat = Literal["normal", "bold", "italic", "underline", "strike", "quote", "code", "spoiler"]
BODY_TAGS = dict(bold="b", italic="i", underline="u", strike="del", quote="blockquote", code="code", spoiler="tg-spoiler")
STYLE_ICONS = {"alert": ("⚠️ ", "📝 ", "👤 ", "💰 "), "lively": ("🚨 ", "📣 ", "🔎 ", "🧾 "), "minimal": ("", "", "", "")}


def units(text):
    return len(text.encode('utf-16-le')) // 2


def source_link(source, source_id, message_id):
    name = source if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{3,31}', source or '') else 'c/'+str(source_id).removeprefix('-100').lstrip('0')
    return f'https://t.me/{name}/{message_id}'


def format_channel_post(text, title, case_id, source_date, url, has_media=False, *, emoji_style="alert", body_format="normal"):
    lines = text.replace('\r\n','\n').replace('\r','\n').split('\n')
    body, people, fields = [], [], []
    current = None
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        i += 1
        # An isolated profile marker can put the numeric ID on the next line.
        if raw == '👤' and i < len(lines) and re.fullmatch(r'\d{5,16}', lines[i].strip()):
            raw += lines[i].strip(); i += 1
        identity = re.fullmatch(r'(?:👤\s*|(?:🆔\s*)?(?:ID|用户ID|Telegram ID)\s*[:：]\s*)(\d{5,16}|\+[\d ()-]{6,25})',raw,re.I)
        name = re.fullmatch(r'(?:👦[🏻🏼🏽🏾🏿]?\s*|(?:👤\s*)?(?:名称|名字|昵称)\s*[:：]\s*)(.+)',raw)
        username = re.fullmatch(r'(?:🌐\s*|(?:🌐\s*)?(?:用户名|Telegram用户名)\s*[:：]\s*)(@?[a-zA-Z][a-zA-Z0-9_]{3,31})',raw)
        if identity:
            current = {'id':identity[1]};people.append(current);continue
        if name or username:
            key,value = ('name',name[1]) if name else ('username',username[1])
            if current is None or key in current:
                current={};people.append(current)
            current[key]=value;continue
        if current is not None:
            note = re.fullmatch(r'👪\s*(.+)',raw)
            if note: current['note']=note[1];continue
            if re.match(r'^(?:[🕑🕒🕐🕰]|(?:最后同步时间|最後同步時間|已更新)\s*[:：]?\s*\d)',raw):continue
            if re.fullmatch(r'[\U0001F1E6-\U0001F1FF]{2}\s*[A-Za-z ]+',raw):continue
        # Move only explicitly labelled fields. Numbers inside a story remain untouched.
        amount = re.fullmatch(r'(涉及金额|被骗金额|损失金额|交易金额)\s*[:：]\s*(.+)',raw)
        address = re.fullmatch(r'(?:相关地址|钱包地址|骗子地址|跑路地址)\s*[:：]\s*([A-Za-z0-9]{20,100})',raw)
        trade_date = re.fullmatch(r'(?:交易时间|交易日期)\s*[:：]\s*(.+)',raw)
        if amount:fields.append((amount[1],amount[2]));current=None;continue
        if address:fields.append(('相关地址',address[1]));current=None;continue
        if trade_date:fields.append(('交易时间',trade_date[1]));current=None;continue
        if raw: current=None
        if not body or raw!=body[-1]:body.append(raw)
    narrative='\n'.join(body).strip()
    when=source_date.replace(tzinfo=timezone.utc) if source_date.tzinfo is None else source_date
    date=when.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y.%m.%d')
    icons = STYLE_ICONS[emoji_style]
    parts=[f'{icons[0]}<b>{html.escape(title)}</b>\n{date} · #曝光记录']
    if narrative:
        content = html.escape(narrative)
        tag = BODY_TAGS.get(body_format)
        if tag: content = f'<{tag}>'+content+f'</{tag}>'
        parts.append(icons[1]+'<b>事件经过</b>\n'+content)
    if people:
        rows=[]
        for person in people:
            values=[]
            if person.get('name'):values.append('名称：'+html.escape(person['name']))
            if person.get('username'):
                user=person['username'].lstrip('@')
                values.append(f'用户名：<a href="https://t.me/{user}">@{user}</a>')
            if person.get('id'):
                label='账号标识' if person['id'].startswith('+') else 'Telegram ID'
                values.append(label+'：<code>'+html.escape(person['id'])+'</code>')
            if person.get('note'):values.append('备注：'+html.escape(person['note']))
            if values:rows.append('\n'.join(values))
        if rows:parts.append(icons[2]+'<b>涉及账号</b>\n'+'\n\n'.join(rows))
    if fields:
        rows=[]
        for label,value in dict.fromkeys(fields):
            value=html.escape(value)
            rows.append(label+'：'+ ('\n<code>'+value+'</code>' if label=='相关地址' else '<b>'+value+'</b>' if '金额' in label else value))
        parts.append(icons[3]+'<b>交易信息</b>\n'+'\n'.join(rows))
    return '\n\n'.join(parts)


def split_post(text, entities, limit=4096):
    """Split on code-point boundaries, then clip UTF-16 entity offsets per part."""
    start, offset = 0, 0
    while start < len(text):
        end,total=start,0
        while end<len(text) and total+units(text[end])<=limit:
            total+=units(text[end]);end+=1
        if end<len(text):
            newline=text.rfind('\n',start,end)
            space=text.rfind(' ',start,end)
            preferred=newline if newline>=start+(end-start)//2 else space
            if preferred>=start+(end-start)//2:end=preferred+1;total=units(text[start:end])
        clipped=[]
        for entity in entities:
            left=max(entity.offset,offset);right=min(entity.offset+entity.length,offset+total)
            if left<right:
                item=copy.copy(entity);item.offset=left-offset;item.length=right-left;clipped.append(item)
        yield text[start:end],clipped
        start,offset=end,offset+total
