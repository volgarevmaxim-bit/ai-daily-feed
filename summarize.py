#!/usr/bin/env python3
"""Саммаризация новых записей через DeepSeek: summary + digest (почти полный перевод) + critique.

Мержит в data/items.json (дедуп по url, сортировка по дате). Только при успешном
healthcheck (каждая запись получила digest или явную ошибку) продвигает state.json
из state_after — упавший LLM не должен съедать непрочитанные записи.
"""
import os, re, sys, json, glob, hashlib, argparse, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
import httpx, trafilatura

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
ITEMS = os.path.join(DATA, "items.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
MAX_TEXT = 12000

SYSTEM = """Ты — редактор персональной ленты чтения. По каждой статье подготовь три вещи на РУССКОМ языке.

Правила:
1. title — точный заголовок статьи. Если исходник на английском, оставь оригинал или дай точный перевод — выбери более информативный вариант.
2. summary — 2–3 предложения: суть материала, почему это значимо.
3. digest — почти полный перевод-пересказ статьи на русский. Сохрани структуру, все ключевые тезисы, цифры, имена, аргументы и выводы. Это не дословный подстрочник, но читатель не должен лезть в оригинал, чтобы понять содержание целиком. Форматируй markdown: короткие абзацы, списки где уместно.
4. critique — критический разбор, СТРОКА (НЕ массив), 3–8 пунктов, разделяй их переводами строк: сильные и слабые стороны материала, что упущено, возможная предвзятость или контринтересы источника, какие утверждения стоит проверить, как это соотносится с контекстом индустрии.

Все четыре поля (title, summary, digest, critique) должны быть строками, не массивами. Верни СТРОГО один JSON-объект с полями title, summary, digest, critique. Никакого текста вне JSON."""


def load_env():
    env = {}
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    # переменные окружения (CI) имеют приоритет
    for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "LLM_MODEL"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def llm(client, env, messages):
    url = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    payload = {
        "model": env.get("LLM_MODEL", "deepseek-chat"),
        "messages": messages,
        "temperature": 0,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
    }
    last = None
    for _ in range(2):
        try:
            r = client.post(url, json=payload, headers={"Authorization": f"Bearer {env['DEEPSEEK_API_KEY']}"})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as ex:
            last = ex
    raise last


def extract_text(client, item):
    html = item.get("content_html", "") or ""
    if len(html) < 400:
        try:
            html = client.get(item["url"]).text
        except Exception:
            html = ""
    txt = ""
    try:
        txt = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    except Exception:
        txt = ""
    txt = txt.strip()
    if not txt:
        txt = re.sub(r"<[^>]+>", " ", html or "")
        txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:MAX_TEXT]


def latest_raw():
    files = [p for p in glob.glob(os.path.join(RAW, "raw_*.json"))]
    files.sort(key=lambda p: os.path.basename(p))
    return files[-1] if files else None


def _dt(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def _sort_dt(x):
    dt = _dt(x.get("published_iso") or x.get("published"))
    return dt.timestamp() if dt else 0


def _to_str(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return str(v)


def _parse_json(s):
    """json.loads + восстановление обрезанного/грязного JSON."""
    try:
        d = json.loads(s)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    out = {}
    for key in ("title", "summary", "digest", "critique"):
        mm = re.search(r'"' + key + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', s)
        if mm:
            try:
                out[key] = json.loads('"' + mm.group(1) + '"')
            except Exception:
                out[key] = mm.group(1)
    if out:
        return out
    raise ValueError("unparseable json")


def process(env, it):
    client = httpx.Client(verify=False, follow_redirects=True, timeout=120, headers={"User-Agent": UA})
    try:
        text = extract_text(client, it)
        if not text:
            return it["url"], {**it, "title": it["title"], "summary": "", "digest": "",
                               "critique": "", "error": "нет текста для извлечения"}
        user = (f"Источник: {it['source']}\n"
                f"Заголовок: {it['title']}\n"
                f"Дата: {it.get('published','')}\n"
                f"URL: {it['url']}\n\nТекст:\n{text}")
        try:
            resp = llm(client, env, [{"role": "system", "content": SYSTEM},
                                     {"role": "user", "content": user}])
            d = _parse_json(resp)
            merged = {**it, "title": _to_str(d.get("title", it["title"])),
                      "summary": _to_str(d.get("summary", "")),
                      "digest": _to_str(d.get("digest", "")),
                      "critique": _to_str(d.get("critique", ""))}
        except Exception as ex:
            merged = {**it, "title": it["title"], "summary": "", "digest": "",
                      "critique": "", "error": str(ex)[:200]}
        return it["url"], merged
    finally:
        client.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="конкретный raw-файл; по умолчанию — последний по дате")
    ap.add_argument("--workers", type=int, default=8, help="параллельных LLM-запросов")
    args = ap.parse_args()

    env = load_env()
    if not env.get("DEEPSEEK_API_KEY"):
        print("НЕТ DEEPSEEK_API_KEY в .env"); sys.exit(1)

    raw_path = args.input or latest_raw()
    if not raw_path:
        print("Нет raw-файлов — сначала запусти fetch.py"); sys.exit(1)
    print("Обрабатываю:", os.path.basename(raw_path))

    raw = json.load(open(raw_path, encoding="utf-8"))
    items = raw["items"]

    existing = json.load(open(ITEMS, encoding="utf-8")) if os.path.exists(ITEMS) else []
    by_url = {i["url"]: i for i in existing}

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process, env, it): it for it in items}
        for fut in as_completed(futures):
            url, merged = fut.result()
            merged.pop("content_html", None)
            by_url[url] = merged
            done += 1
            flag = "" if merged.get("digest") else "  (error)"
            print(f"[{done}/{len(items)}]{flag} {merged.get('source')}: {merged.get('title','')[:55]}")

    # healthcheck: каждая запись должна иметь digest или явную ошибку
    bad = [it["url"] for it in items
           if not by_url[it["url"]].get("digest") and not by_url[it["url"]].get("error")]
    if bad:
        print("HEALTHCHECK FAIL:", len(bad), "записей без digest и без error; state НЕ продвинут")
        sys.exit(1)

    all_items = sorted(by_url.values(), key=_sort_dt, reverse=True)
    json.dump(all_items, open(ITEMS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    state_path = os.path.join(DATA, "state.json")
    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
    state.update(raw.get("state_after", {}))
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"OK: items.json = {len(all_items)} записей, state продвинут")


if __name__ == "__main__":
    main()
