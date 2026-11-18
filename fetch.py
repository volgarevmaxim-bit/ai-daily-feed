#!/usr/bin/env python3
"""Сбор новых записей из sources.yaml (feed или sitemap). Пишет data/raw/raw_<date>.json.

Инварианты:
- state.json коммитится ТОЛЬКО в summarize.py после успешной классификации; здесь
  новый state сохраняется как state_after внутри raw-файла (self-healing refetch).
- seed-прогон: саммаризируем top-N на источник, но помечаем ВСЕ текущие url как seen.
- ключ инкремента — url (надёжнее, чем дата).
"""
import os, re, sys, json, argparse, hashlib, datetime
from email.utils import parsedate_to_datetime
import httpx, feedparser, yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def load_sources():
    d = yaml.safe_load(open(os.path.join(ROOT, "sources.yaml"), encoding="utf-8"))
    return [s for s in d["sources"] if s.get("enabled", True)]


def load_state():
    p = os.path.join(DATA, "state.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s):
    """RFC822 / ISO-8601 / date-only -> aware datetime или None."""
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


def iso_of(pub):
    dt = parse_dt(pub)
    return dt.isoformat() if dt else ""


def sort_key(x):
    dt = parse_dt(x["published"])
    return dt.timestamp() if dt else 0


def fetch_feed(client, src):
    r = client.get(src["url"])
    r.raise_for_status()
    f = feedparser.parse(r.content)
    out = []
    for e in f.entries:
        url = (e.get("link") or e.get("id") or "").strip()
        if not url:
            continue
        content = ""
        if e.get("content"):
            content = e["content"][0].get("value", "")
        elif e.get("summary"):
            content = e.get("summary", "")
        published = e.get("published") or e.get("updated") or ""
        out.append(dict(
            url=url,
            title=(e.get("title") or "").strip(),
            published=published,
            published_iso=iso_of(published),
            content_html=content,
            source=src["name"],
            category=src["category"],
        ))
    return out


def fetch_sitemap(client, src):
    r = client.get(src["url"])
    r.raise_for_status()
    xml = r.text
    blocks = re.findall(r"<url>\s*<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", xml)
    out = []
    pf = src.get("path_filter", "")
    for u, d in blocks:
        if pf and pf not in u:
            continue
        slug = u.rstrip("/").split("/")[-1]
        title = " ".join(re.split(r"[-_]+", slug)).strip()
        out.append(dict(
            url=u, title=title, published=d or "", published_iso=iso_of(d or ""),
            content_html="", source=src["name"], category=src["category"],
        ))
    return out


def fetch_scrape(client, src):
    """Парсит HTML-страницу по regex, извлекает ссылки на элементы."""
    r = client.get(src["url"])
    r.raise_for_status()
    html = r.text
    ids = []
    for m in re.finditer(src["regex"], html):
        v = m.group(1)
        if v not in ids:
            ids.append(v)
    out = []
    for v in ids:
        url = src["link_prefix"] + v
        title = v
        if src.get("title_from_slug"):
            title = " ".join(re.split(r"[-_]+", v)).strip()
        published = ""
        if src.get("date_from_arxiv_id"):
            try:
                yymm = v.split(".")[0]
                published = f"20{yymm[:2]}-{yymm[2:4]}-01"
            except Exception:
                published = ""
        out.append(dict(
            url=url, title=title, published=published, published_iso=iso_of(published),
            content_html="", source=src["name"], category=src["category"],
        ))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="первый прогон: top-N на источник, все url -> seen")
    ap.add_argument("--limit", type=int, default=8, help="сколько статей саммаризировать на источник при seed")
    args = ap.parse_args()

    sources = load_sources()
    state = load_state()
    client = httpx.Client(verify=False, follow_redirects=True, timeout=40, headers={"User-Agent": UA})

    new_items = []
    state_after = dict(state)
    for src in sources:
        name = src["name"]
        try:
            if src["type"] == "feed":
                items = fetch_feed(client, src)
            elif src["type"] == "sitemap":
                items = fetch_sitemap(client, src)
            else:
                items = fetch_scrape(client, src)
        except Exception as ex:
            print(f"[{name}] FETCH ERROR: {str(ex)[:120]}")
            continue
        items.sort(key=sort_key, reverse=True)
        seen = set(state.get(name, {}).get("seen", []))
        if args.seed:
            candidates = items[:args.limit]
        else:
            candidates = [i for i in items if i["url"] not in seen]
        for it in candidates:
            it["id"] = hashlib.md5(it["url"].encode()).hexdigest()[:12]
        new_items.extend(candidates)
        state_after[name] = {"seen": sorted({u["url"] for u in items}), "updated": iso_now()}
        print(f"[{name}] total={len(items)} new={len(candidates)}")

    client.close()

    os.makedirs(RAW, exist_ok=True)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    out = {"fetched_at": iso_now(), "seed": args.seed, "items": new_items, "state_after": state_after}
    path = os.path.join(RAW, f"raw_{today}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nWROTE {path}  ({len(new_items)} новых записей)")


if __name__ == "__main__":
    main()
