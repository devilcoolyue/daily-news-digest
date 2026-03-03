from __future__ import annotations

import json
import ipaddress
import os
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .models import DomainConfig, Event


ALLOWED_ICON_KINDS = {
    "openai",
    "meta",
    "google",
    "anthropic",
    "minimax",
    "apple",
    "trend",
    "chip",
    "robot",
    "megaphone",
    "brain",
    "spark",
}

ICON_ALIASES = {
    "logo_openai": "openai",
    "open-ai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
    "gpt4": "openai",
    "gpt_4": "openai",
    "sora": "openai",
    "logo_meta": "meta",
    "llama": "meta",
    "logo_google": "google",
    "gemini": "google",
    "deepmind": "google",
    "logo_anthropic": "anthropic",
    "claude": "anthropic",
    "logo_minimax": "minimax",
    "logo_apple": "apple",
    "siri": "apple",
    "brand": "spark",
    "brand_logo": "spark",
    "robotaxi": "robot",
    "chart": "trend",
    "chipset": "chip",
    "speaker": "megaphone",
}

ICON_URL_BY_KIND = {
    "openai": "https://www.google.com/s2/favicons?domain=openai.com&sz=128",
    "meta": "https://www.google.com/s2/favicons?domain=meta.com&sz=128",
    "google": "https://www.google.com/s2/favicons?domain=google.com&sz=128",
    "anthropic": "https://www.google.com/s2/favicons?domain=anthropic.com&sz=128",
    "minimax": "https://www.google.com/s2/favicons?domain=minimax.chat&sz=128",
    "apple": "https://www.google.com/s2/favicons?domain=apple.com&sz=128",
    "trend": "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f4c8.png",
    "chip": "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f9be.png",
    "robot": "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f916.png",
    "megaphone": "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f4e3.png",
    "brain": "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f9e0.png",
    "spark": "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/2728.png",
}


def _strip_ellipsis(text: str) -> str:
    return text.replace("…", "").replace("...", "").strip()


def _compact(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()).strip()


def _trim_for_prompt(text: str, max_chars: int) -> str:
    clean = _compact(text)
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip()


def normalize_icon_kind(value: str) -> str:
    key = _compact(value).lower().replace(" ", "_").replace("-", "_")
    key = ICON_ALIASES.get(key, key)
    if key in ALLOWED_ICON_KINDS:
        return key
    return ""


def sanitize_icon_url(value: str) -> str:
    raw = _compact(value)
    if not raw or len(raw) > 320:
        return ""

    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return ""

    if parsed.scheme.lower() != "https":
        return ""

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return ""
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return ""

    try:
        addr = ipaddress.ip_address(host)
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
        ):
            return ""
    except ValueError:
        pass

    normalized = parsed._replace(fragment="")
    return urllib.parse.urlunparse(normalized)


def _cjk_ratio(text: str) -> float:
    clean = text or ""
    if not clean:
        return 0.0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", clean))
    alpha = len(re.findall(r"[A-Za-z]", clean))
    return cjk / max(1, cjk + alpha)


def _is_chinese_readable(text: str, min_ratio: float, min_cjk: int) -> bool:
    clean = _compact(text or "")
    cjk = len(re.findall(r"[\u4e00-\u9fff]", clean))
    alpha = len(re.findall(r"[A-Za-z]", clean))
    if cjk < min_cjk:
        return False
    ratio = cjk / max(1, cjk + alpha)
    # Allow brand-heavy phrases with enough Chinese context.
    return ratio >= min_ratio or (cjk >= min_cjk + 2 and alpha <= 10)


def _extract_json_value(text: str) -> Any:
    raw = text.strip()
    if not raw:
        return {}

    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    for pattern in (r"\[\s*\{.*\}\s*\]", r"\{.*\}"):
        match = re.search(pattern, raw, flags=re.DOTALL)
        if not match:
            continue

        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue

    return {}


def _sanitize_title(title: str, max_chars: int, min_chars: int) -> str:
    text = _compact(_strip_ellipsis(title))
    text = re.sub(r"[“”\"'`]", "", text)
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
    if has_cjk:
        text = re.sub(r"\s+", "", text)
    else:
        text = re.sub(r"\s+", " ", text)
    text = text.strip("，,。；;：:!！?？-—_")
    if not text:
        return ""

    if len(text) > max_chars:
        if has_cjk:
            trimmed = text[:max_chars].rstrip("，,。；;：:!！?？-—_")
            # Avoid leaving half-cut latin tokens like "Cl"/"Si" at the end.
            no_tail = re.sub(r"[A-Za-z0-9][A-Za-z0-9&'._-]*$", "", trimmed).rstrip("，,。；;：:!！?？-—_ ")
            text = no_tail if len(no_tail) >= min_chars else trimmed
        else:
            clip = text[: max_chars + 1]
            if " " in clip:
                text = clip[: clip.rfind(" ")].rstrip("，,。；;：:!！?？-—_ ")
            if len(text) > max_chars:
                text = text[:max_chars].rstrip("，,。；;：:!！?？-—_ ")
    if len(text) < min_chars:
        return ""
    return text


def _sanitize_summary(summary: str, max_chars: int, min_chars: int) -> str:
    text = _compact(_strip_ellipsis(summary))
    text = re.sub(r"[“”\"'`]", "", text)
    text = text.strip("，,。；;：:!！?？-—_")
    if not text:
        return ""

    if len(text) > max_chars:
        # Keep clause-level truncation first, then hard cut if needed.
        parts = re.split(r"[，,；;：:]", text)
        packed = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            candidate = part if not packed else packed + "，" + part
            if len(candidate) <= max_chars:
                packed = candidate
            else:
                break
        if packed:
            text = packed
        if len(text) > max_chars:
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
            if has_cjk:
                text = text[:max_chars].rstrip("，,。；;：:!！?？-—_")
            else:
                clip = text[: max_chars + 1]
                if " " in clip:
                    text = clip[: clip.rfind(" ")].rstrip("，,。；;：:!！?？-—_ ")
                if len(text) > max_chars:
                    text = text[:max_chars].rstrip("，,。；;：:!！?？-—_ ")

    if len(text) < min_chars:
        return ""
    return text


def heuristic_refine_title(raw_title: str, max_chars: int, min_chars: int) -> str:
    text = _compact(_strip_ellipsis(raw_title))
    if not text:
        return ""

    parts = re.split(r"[，,。；;：:（）()【】\[\]|/]", text)
    candidates = [p.strip() for p in parts if p.strip()]
    for candidate in candidates:
        cleaned = _sanitize_title(candidate, max_chars, min_chars)
        if cleaned:
            return cleaned

    return _sanitize_title(text, max_chars, min_chars)


def heuristic_refine_summary(raw_summary: str, fallback_title: str, max_chars: int, min_chars: int) -> str:
    source = _compact(_strip_ellipsis(raw_summary or ""))
    if not source:
        source = _compact(_strip_ellipsis(fallback_title))

    if not source:
        return ""

    sentences = re.split(r"[。！？!?.]", source)
    packed = ""
    for sentence in sentences:
        sentence = sentence.strip(" ，,；;：:")
        if not sentence:
            continue
        candidate = sentence if not packed else packed + "，" + sentence
        if len(candidate) <= max_chars:
            packed = candidate
        else:
            break

    if not packed:
        packed = source

    refined = _sanitize_summary(packed, max_chars=max_chars, min_chars=min_chars)
    if refined:
        return refined

    return _sanitize_summary(source, max_chars=max_chars, min_chars=min_chars)


def heuristic_pick_icon_kind(title: str, summary: str, tags: list[str]) -> str:
    blob = f"{title} {summary} {' '.join(tags)}".lower()

    if any(k in blob for k in ["openai", "chatgpt", "gpt-4", "gpt4", "sora"]):
        return "openai"
    if any(k in blob for k in ["meta", "llama"]):
        return "meta"
    if any(k in blob for k in ["google", "gemini", "deepmind"]):
        return "google"
    if any(k in blob for k in ["anthropic", "claude"]):
        return "anthropic"
    if "minimax" in blob:
        return "minimax"
    if any(k in blob for k in ["apple", "苹果", "siri"]):
        return "apple"
    if any(k in blob for k in ["财报", "营收", "融资", "增长", "首超", "trend"]):
        return "trend"
    if any(k in blob for k in ["芯片", "gpu", "nvidia", "算力"]):
        return "chip"
    if any(k in blob for k in ["robotaxi", "机器人", "自动驾驶", "车队"]):
        return "robot"
    if any(k in blob for k in ["发布", "新品", "活动", "launch", "event"]):
        return "megaphone"
    if any(k in blob for k in ["模型", "ai", "大模型"]):
        return "brain"
    return "spark"


def heuristic_pick_icon_url(
    title: str,
    summary: str,
    tags: list[str],
    entity: str,
    icon_kind: str,
) -> str:
    blob = f"{title} {summary} {' '.join(tags)} {entity}".lower()

    if any(k in blob for k in ["openai", "chatgpt", "gpt4", "gpt-4", "sora"]):
        return ICON_URL_BY_KIND["openai"]
    if any(k in blob for k in ["meta", "llama"]):
        return ICON_URL_BY_KIND["meta"]
    if any(k in blob for k in ["google", "gemini", "deepmind"]):
        return ICON_URL_BY_KIND["google"]
    if any(k in blob for k in ["anthropic", "claude"]):
        return ICON_URL_BY_KIND["anthropic"]
    if "minimax" in blob:
        return ICON_URL_BY_KIND["minimax"]
    if any(k in blob for k in ["apple", "苹果", "siri"]):
        return ICON_URL_BY_KIND["apple"]

    if icon_kind in ICON_URL_BY_KIND:
        return ICON_URL_BY_KIND[icon_kind]
    return ICON_URL_BY_KIND["spark"]


def _build_prompt(
    event: Event,
    title_max_chars: int,
    title_min_chars: int,
    summary_max_chars: int,
    summary_min_chars: int,
) -> str:
    tags = "、".join(event.tags) if event.tags else "无"
    icons = ", ".join(sorted(ALLOWED_ICON_KINDS))
    return (
        "你是中文科技资讯编辑。请根据输入输出一个卡片信息，必须是JSON。\n"
        "输出格式："
        "{\"title\":\"...\",\"summary\":\"...\",\"icon_kind\":\"...\",\"icon_url\":\"...\"}\n"
        "要求：\n"
        f"1) title 长度 {title_min_chars}-{title_max_chars} 字，核心主体+动作，不要省略号\n"
        f"2) summary 长度 {summary_min_chars}-{summary_max_chars} 字，信息完整，不要省略号\n"
        f"3) icon_kind 必须从以下枚举中选择：{icons}\n"
        "4) icon_url 必须是可公开访问的 HTTPS 图片直链，优先透明背景 PNG/WebP/JPG\n"
        "5) 如果是品牌主体新闻，优先选择对应品牌logo样式 icon_kind\n"
        "6) 不得编造事实\n"
        f"标题原文：{event.canonical_title}\n"
        f"摘要原文：{event.summary}\n"
        f"标签：{tags}\n"
    )


def _build_batch_prompt(
    batch_inputs: list[dict[str, Any]],
) -> str:
    icons = ", ".join(sorted(ALLOWED_ICON_KINDS))
    records = []
    for row in batch_inputs:
        records.append(
            {
                "event_id": row["event_id"],
                "title_raw": _trim_for_prompt(str(row["title_raw"]), 120),
                "summary_raw": _trim_for_prompt(str(row["summary_raw"]), 240),
                "tags": row["tags"],
                "entity": row.get("entity", ""),
                "primary_source": row.get("primary_source", ""),
                "article_url": row.get("article_url", ""),
                "title_min_chars": row["title_min_chars"],
                "title_max_chars": row["title_max_chars"],
                "summary_min_chars": row["summary_min_chars"],
                "summary_max_chars": row["summary_max_chars"],
            }
        )

    return (
        "你是中文科技资讯编辑。请批量处理卡片并返回JSON数组，不要输出任何额外文本。\n"
        "输出格式："
        "[{\"event_id\":\"...\",\"title\":\"...\",\"summary\":\"...\",\"icon_kind\":\"...\",\"icon_url\":\"...\"}]\n"
        "规则：\n"
        "1) 每条必须保留输入的 event_id\n"
        "2) title/summary 必须遵守该条提供的最小和最大字数限制\n"
        "3) title 要提炼核心主体+动作，不要省略号\n"
        "4) summary 要完整且可读，不要省略号，不编造事实\n"
        "5) title 与 summary 必须使用简体中文表达，品牌名/产品名可保留英文\n"
        f"6) icon_kind 只能从以下枚举选择：{icons}\n"
        "7) icon_url 必须返回公开可访问的 HTTPS 图片直链（优先透明背景 PNG/WebP/JPG）\n"
        "8) 品牌主体新闻优先用品牌logo样式 icon_kind\n"
        "输入数据：\n"
        f"{json.dumps(records, ensure_ascii=False)}"
    )


def _build_chinese_rewrite_prompt(
    batch_inputs: list[dict[str, Any]],
) -> str:
    records = []
    for row in batch_inputs:
        records.append(
            {
                "event_id": row["event_id"],
                "title_raw": _trim_for_prompt(str(row["title_raw"]), 120),
                "summary_raw": _trim_for_prompt(str(row["summary_raw"]), 220),
                "title_draft": _trim_for_prompt(str(row.get("title_draft", "")), 80),
                "summary_draft": _trim_for_prompt(str(row.get("summary_draft", "")), 160),
                "title_min_chars": row["title_min_chars"],
                "title_max_chars": row["title_max_chars"],
                "summary_min_chars": row["summary_min_chars"],
                "summary_max_chars": row["summary_max_chars"],
            }
        )

    return (
        "请把以下资讯草稿重写为简体中文，返回JSON数组，不要输出任何额外文本。\n"
        "输出格式："
        "[{\"event_id\":\"...\",\"title\":\"...\",\"summary\":\"...\"}]\n"
        "要求：\n"
        "1) 逐条保留 event_id\n"
        "2) title/summary 必须是简体中文，品牌名和产品名可保留英文\n"
        "3) title 需凝练、可读，不要省略号\n"
        "4) summary 需完整通顺，不要省略号，不编造事实\n"
        "5) 每条必须满足输入中的最小/最大字数限制\n"
        "输入数据：\n"
        f"{json.dumps(records, ensure_ascii=False)}"
    )


def _parse_batch_payload(value: Any) -> dict[str, dict[str, str]]:
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            rows = value.get("items", [])
        else:
            rows = [value]
    elif isinstance(value, list):
        rows = value
    else:
        rows = []

    out: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id", "")).strip()
        if not event_id:
            continue
        out[event_id] = {
            "title": str(row.get("title", "")),
            "summary": str(row.get("summary", "")),
            "icon_kind": str(row.get("icon_kind", "")),
            "icon_url": str(row.get("icon_url", "")),
        }
    return out


def _call_chat_completion(
    api_base: str,
    model: str,
    api_key: str,
    prompt: str,
    timeout_sec: int,
    max_tokens: int = 220,
) -> str:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严谨的中文资讯编辑。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    choices = body.get("choices", [])
    if not choices:
        return ""
    content = choices[0].get("message", {}).get("content", "")
    return str(content or "")


def enrich_events(
    events: list[Event],
    config: DomainConfig,
    budgets: list[dict[str, int]],
) -> dict[str, dict[str, str]]:
    llm_cfg: dict[str, Any] = config.llm or {}
    default_title_max = int(llm_cfg.get("title_max_chars", config.title_max_len))
    default_title_min = int(llm_cfg.get("title_min_chars", 6))
    default_summary_max = int(llm_cfg.get("summary_max_chars", config.summary_max_len))
    default_summary_min = int(llm_cfg.get("summary_min_chars", 20))

    enable_title = bool(llm_cfg.get("enable_title_refine", False))
    enable_summary = bool(llm_cfg.get("enable_summary_refine", True))
    enable_icon = bool(llm_cfg.get("enable_icon_classify", True))
    enable_llm = enable_title or enable_summary or enable_icon

    api_key = os.getenv("LLM_API_KEY", "").strip() or os.getenv("DEEPSEEK_API_KEY", "").strip()
    api_base = str(llm_cfg.get("api_base", "https://api.deepseek.com/v1"))
    model = str(llm_cfg.get("model", "deepseek-chat"))
    timeout_sec = int(llm_cfg.get("timeout_sec", 20))
    batch_size = int(llm_cfg.get("batch_size", max(1, len(events))))
    if batch_size <= 0:
        batch_size = max(1, len(events))

    out: dict[str, dict[str, str]] = {}
    event_rows: list[dict[str, Any]] = []

    for idx, event in enumerate(events):
        budget = budgets[idx] if idx < len(budgets) else {}
        title_max = int(budget.get("title_max_chars", default_title_max))
        title_min = min(default_title_min, max(4, title_max))
        summary_max = int(budget.get("summary_max_chars", default_summary_max))
        summary_min = min(default_summary_min, max(8, summary_max))

        event_rows.append(
            {
                "event_id": event.event_id,
                "event": event,
                "title_min_chars": title_min,
                "title_max_chars": title_max,
                "summary_min_chars": summary_min,
                "summary_max_chars": summary_max,
                "title_raw": event.canonical_title,
                "summary_raw": event.summary,
                "tags": event.tags,
                "entity": event.entity,
                "primary_source": event.primary_source_name,
                "article_url": event.urls[0] if event.urls else "",
            }
        )

    if enable_llm and api_key and event_rows:
        for start in range(0, len(event_rows), batch_size):
            batch = event_rows[start : start + batch_size]
            prompt = _build_batch_prompt(batch)
            try:
                estimated = sum(int(r["title_max_chars"]) + int(r["summary_max_chars"]) + 36 for r in batch)
                max_tokens = max(480, min(2000, int(estimated * 1.35)))
                llm_raw = _call_chat_completion(
                    api_base=api_base,
                    model=model,
                    api_key=api_key,
                    prompt=prompt,
                    timeout_sec=timeout_sec,
                    max_tokens=max_tokens,
                )
                payload = _extract_json_value(llm_raw)
                parsed = _parse_batch_payload(payload)
                for row in batch:
                    event_id = str(row["event_id"])
                    raw = parsed.get(event_id, {})
                    out[event_id] = {
                        "title": _sanitize_title(
                            str(raw.get("title", "")),
                            max_chars=int(row["title_max_chars"]),
                            min_chars=int(row["title_min_chars"]),
                        ),
                        "summary": _sanitize_summary(
                            str(raw.get("summary", "")),
                            max_chars=int(row["summary_max_chars"]),
                            min_chars=int(row["summary_min_chars"]),
                        ),
                        "icon_kind": normalize_icon_kind(str(raw.get("icon_kind", ""))),
                        "icon_url": sanitize_icon_url(str(raw.get("icon_url", ""))),
                    }
            except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
                # Batch failed: keep empty for heuristic fallback.
                continue

        # Second pass: rewrite low-readability English outputs into Chinese, still in batches.
        rewrite_rows: list[dict[str, Any]] = []
        for row in event_rows:
            event_id = str(row["event_id"])
            payload = out.get(event_id, {})
            title = str(payload.get("title", ""))
            summary = str(payload.get("summary", ""))
            if _is_chinese_readable(title, min_ratio=0.20, min_cjk=2) and _is_chinese_readable(
                summary, min_ratio=0.25, min_cjk=6
            ):
                continue
            rewrite_rows.append(
                {
                    **row,
                    "title_draft": title,
                    "summary_draft": summary,
                }
            )

        for start in range(0, len(rewrite_rows), batch_size):
            batch = rewrite_rows[start : start + batch_size]
            prompt = _build_chinese_rewrite_prompt(batch)
            try:
                estimated = sum(int(r["title_max_chars"]) + int(r["summary_max_chars"]) + 30 for r in batch)
                max_tokens = max(450, min(1800, int(estimated * 1.25)))
                llm_raw = _call_chat_completion(
                    api_base=api_base,
                    model=model,
                    api_key=api_key,
                    prompt=prompt,
                    timeout_sec=timeout_sec,
                    max_tokens=max_tokens,
                )
                payload = _extract_json_value(llm_raw)
                parsed = _parse_batch_payload(payload)
                for row in batch:
                    event_id = str(row["event_id"])
                    raw = parsed.get(event_id, {})
                    title = _sanitize_title(
                        str(raw.get("title", "")),
                        max_chars=int(row["title_max_chars"]),
                        min_chars=int(row["title_min_chars"]),
                    )
                    summary = _sanitize_summary(
                        str(raw.get("summary", "")),
                        max_chars=int(row["summary_max_chars"]),
                        min_chars=int(row["summary_min_chars"]),
                    )
                    current = out.get(event_id, {})
                    out[event_id] = {
                        "title": title or str(current.get("title", "")),
                        "summary": summary or str(current.get("summary", "")),
                        "icon_kind": str(current.get("icon_kind", "")),
                        "icon_url": str(current.get("icon_url", "")),
                    }
            except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
                continue

    for row in event_rows:
        event = row["event"]
        event_id = str(row["event_id"])
        title_min = int(row["title_min_chars"])
        title_max = int(row["title_max_chars"])
        summary_min = int(row["summary_min_chars"])
        summary_max = int(row["summary_max_chars"])
        payload = out.get(event_id, {})
        title = str(payload.get("title", ""))
        summary = str(payload.get("summary", ""))
        icon_kind = str(payload.get("icon_kind", ""))
        icon_url = sanitize_icon_url(str(payload.get("icon_url", "")))
        if not title:
            title = heuristic_refine_title(event.canonical_title, max_chars=title_max, min_chars=title_min)
        if not summary:
            summary = heuristic_refine_summary(
                event.summary,
                fallback_title=event.canonical_title,
                max_chars=summary_max,
                min_chars=summary_min,
            )
        if not icon_kind:
            icon_kind = heuristic_pick_icon_kind(title or event.canonical_title, summary or event.summary, event.tags)
        if not icon_url:
            icon_url = sanitize_icon_url(
                heuristic_pick_icon_url(
                    title=title or event.canonical_title,
                    summary=summary or event.summary,
                    tags=event.tags,
                    entity=event.entity,
                    icon_kind=icon_kind,
                )
            )

        out[event_id] = {
            "title": title,
            "summary": summary,
            "icon_kind": icon_kind,
            "icon_url": icon_url,
        }

    return out
