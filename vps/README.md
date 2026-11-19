# Feedback endpoint — развёртывание на VPS

Принимает оценки ленты (+/−) и отдаёт профиль предпочтений, который `summarize.py`
подмешивает в промпт генерации. Только stdlib, без зависимостей.

## 1. Скопировать и запустить

```bash
scp vps/server.py root@<VPS-IP>:/opt/feed/server.py
ssh root@<VPS-IP> 'python3 /opt/feed/server.py 8099'
```

Проверка: `curl http://<VPS-IP>:8099/profile` → `{"total": 0, "sources": []}`.

## 2. Автозапуск (systemd)

`/etc/systemd/system/feed.service`:

```ini
[Unit]
Description=AI Daily Feed feedback endpoint
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/feed/server.py 8099
Restart=always
WorkingDirectory=/opt/feed

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now feed
```

## 3. Открыть порт (если ufw)

```bash
ufw allow 8099/tcp
```

## Endpoints

- `POST /feedback` — батч оценок: `[{"id","v"(+1/-1),"ts","title","source","url"}, ...]` (upsert по id)
- `GET /profile` — `{"total": N, "sources": [{"source","pos","neg","score"}, ...]}` (sorted по score)

## Куда вписать URL

1. В `index.html`: константа `FEEDBACK_URL` (публичный URL, например `http://<VPS-IP>:8099`).
2. В `summarize.py` через переменную окружения `FEEDBACK_URL` (в Actions — repository variable).
