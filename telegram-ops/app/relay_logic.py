import re
from dataclasses import dataclass
from app.relay_models import RelayTask

USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
TAIL = re.compile(r"-@([A-Za-z][A-Za-z0-9_]{3,31})$")


def words(value: str) -> list[str]:
    return [
        x.strip().casefold() for x in re.split(r"[,，\n]+", value or "") if x.strip()
    ]


def single_line(value: str) -> str:
    return " ".join(value.split())


def filter_message(
    task: RelayTask,
    text: str,
    username: str,
    user_id: int | None = None,
    is_bot: bool = False,
) -> tuple[bool, str]:
    if is_bot:
        return False, "机器人消息"
    if not USERNAME.fullmatch(username or ""):
        return False, "发言者没有可用用户名"
    ignored = [w.lstrip("@") for w in words(task.ignore_users)]
    if username.casefold() in ignored or str(user_id) in ignored:
        return False, "用户在忽略名单中"
    lowered = text.casefold()
    excluded = [w for w in words(task.exclude_keywords) if w in lowered]
    if excluded:
        return False, "命中排除词：" + "、".join(excluded)
    keys = words(task.keywords)
    matched = [
        w
        for w in keys
        if (lowered.strip() == w if task.match_mode == "exact" else w in lowered)
    ]
    ok = bool(matched) and (task.match_mode != "all" or len(matched) == len(keys))
    return ok, ("命中：" + "、".join(matched)) if ok else "未命中关键词"


def format_relay(title: str, text: str, username: str) -> str:
    if not USERNAME.fullmatch(username):
        raise ValueError("无效用户名")
    result = f"{single_line(title)}-{single_line(text)}-@{username}"
    if len(result.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("格式化后的消息超过 Telegram 长度限制")
    return result


@dataclass
class RelayMessage:
    title: str
    text: str
    username: str


def parse_relay(text: str) -> RelayMessage | None:
    # The final -@ field is authoritative, mentions inside the body are ignored.
    match = TAIL.search(text)
    if not match or "\n" in text or "\r" in text:
        return None
    prefix = text[: match.start()]
    if "-" not in prefix:
        return None
    title, body = prefix.split("-", 1)
    if not title.strip() or not body.strip():
        return None
    return RelayMessage(title, body, match.group(1))


def render_copy(template: str, username: str) -> str:
    # No executable template language for user-supplied content.
    return template.replace("{{ username }}", "@" + username).replace(
        "{{username}}", "@" + username
    )
