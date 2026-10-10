"""AI summary provider and prompt construction.

The endpoint follows the OpenAI-compatible chat-completions shape so the
operator can use OpenAI or a compatible provider without exposing the key.
"""
import json
import urllib.request


def build_prompt(messages: list[dict]) -> str:
    lines = [
        "请用简体中文总结下面的 Telegram 群组消息。",
        "按主题归纳，列出重要事项、待跟进问题和明确的行动项。",
        "不要编造消息中没有的信息，控制在 800 字以内。",
        "",
    ]
    for item in messages:
        lines.append(f"[{item['created_at']}] {item['chat_title']} · {item['sender_name']}: {item['text']}")
    return "\n".join(lines)


def request_summary(api_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个严谨的群组运营摘要助手。"},
            {"role": "user", "content": build_prompt(messages)},
        ],
        "temperature": 0.2,
    }).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("AI 接口返回格式不正确") from exc
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    content = str(content).strip()
    if not content:
        raise ValueError("AI 接口返回了空摘要")
    return content
