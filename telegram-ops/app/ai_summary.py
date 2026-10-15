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
        descriptions = {429: "AI 接口额度不足或请求过于频繁，请在服务商后台查看额度和限制", 503: "AI 服务暂不可用（HTTP 503），请在服务商后台检查模型渠道，稍后重试", 404: "AI 接口或模型未找到（HTTP 404），请检查完整地址与模型 ID"}
        raise ValueError(descriptions.get(exc.code, f"AI 接口请求失败（HTTP {exc.code}）")) from exc
    except (TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("AI 接口超时或返回内容格式异常，请稍后重试") from exc
    except urllib.error.URLError as exc:
        raise ValueError("AI 接口暂时无法连接，请检查接口地址和服务器网络") from exc
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("AI 接口返回格式不正确") from exc
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        raise ValueError("AI 接口返回了空摘要")
    content = content.strip()
    if not content:
        raise ValueError("AI 接口返回了空摘要")
    return content
