"""AI summary provider and prompt construction.

The endpoint follows the OpenAI-compatible chat-completions shape so the
operator can use OpenAI or a compatible provider without exposing the key.
"""
import json
import urllib.error
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
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("AI 接口鉴权失败，请检查 API Key 是否正确或已过期") from exc
        raise ValueError(f"AI 接口请求失败（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise ValueError("AI 接口暂时无法连接，请检查接口地址和服务器网络") from exc
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
