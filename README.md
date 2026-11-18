# AI Daily Feed

Персональная лента чтения: свежие записи из блогов AI-мейджоров и лидеров мнений.
К каждой записи — короткое саммари, почти полный перевод-пересказ и критический разбор
(всё генерирует DeepSeek). Статический сайт, обновляется несколько раз в день.

## Локальный запуск

Лента уже собрана (`data/items.json` — 72 записи). Чтобы посмотреть:

```
run.bat
```

и открой http://127.0.0.1:8780/

## Обновление ленты (вручную, локально)

```
.venv\Scripts\python.exe fetch.py          # собрать новые записи
.venv\Scripts\python.exe summarize.py      # саммаризировать → data/items.json
```

Первый (seed) прогон: `fetch.py --seed --limit 6` — берёт последние N статей на источник.

## Структура

| Файл | Назначение |
|---|---|
| `sources.yaml` | источники: `feed` (RSS/Atom), `sitemap` (sitemap.xml + path_filter) или `scrape` (regex из HTML) |
| `fetch.py` | сбор новых записей → `data/raw/raw_<дата>.json` |
| `summarize.py` | LLM-саммари (summary + digest + critique) → `data/items.json` |
| `index.html` | фронт (один файл, vanilla JS, тёмная/светлая тема) |
| `data/items.json` | каноническая лента (сортировка по дате) |
| `data/state.json` | `seen`-URL по источнику (инкремент), коммитится только после успешной классификации |
| `.env` | `DEEPSEEK_API_KEY` (не коммитить) |

## Ключевые инварианты

- `state.json` продвигается **только** в `summarize.py` после healthcheck — упавший LLM не съедает непрочитанные записи (self-healing refetch).
- Ключ инкремента — URL (надёжнее даты); даты нормализуются в `published_iso` для сортировки.
- Seed: саммаризируются top-N на источник, но все текущие URL помечаются seen.

## Источники (8 активных, лонгриды)

RSS/Atom: OpenRouter Blog, Google DeepMind, Hugging Face, Simon Willison, Евгений Кузнецов (Substack).
Sitemap-scrape: Anthropic Research (`/research/`).
HTML-scrape: alphaXiv Trending (трендовые arXiv-статьи с главной), Claude Academy (новые курсы).

## Перенос на GitHub Pages (следующий шаг)

1. Public-репозиторий, Pages → Deploy from branch → `main` (корень).
2. `DEEPSEEK_API_KEY` в Secrets.
3. Workflow: cron 4×/день → `fetch.py` → `summarize.py` → commit+push → Pages.
