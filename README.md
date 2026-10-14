# Мои платежи

Self-hosted трекер подписок и регулярных платежей: когда и сколько платить, что просрочено, сколько уходит в месяц и в год.

- Кнопка «Оплачено» переносит дату на следующий период (неделя, месяц, квартал, год), с отменой
- Группы «Просрочено / На этой неделе / В ближайший месяц / Позже», календарь месяца, расходы по категориям
- Рубли, доллары и евро считаются отдельно
- Работает с телефона, светлая и тёмная тема

Один файл на Python, только стандартная библиотека, данные в SQLite. В простое занимает около 25 МБ памяти и не нагружает процессор.

## Быстрый старт

Нужен Python 3.10+.

```sh
git clone <url> platezhi && cd platezhi
python3 server.py
```

Откройте http://127.0.0.1:8095.

## Настройки

Задаются переменными окружения. Для systemd удобно положить их в `.env` (пример: `.env.example`, сам `.env` в git не попадает).

| Переменная | По умолчанию | |
|---|---|---|
| `PLATEZHI_PORT` | `8095` | порт |
| `PLATEZHI_HOST` | `127.0.0.1` | `0.0.0.0` — открыть для локальной сети / VPN |
| `PLATEZHI_PASSWORD` | — | если задан, страница спросит пароль (имя пользователя любое) |
| `PLATEZHI_DB` | `./data/platezhi.db` | файл базы |

Открываете доступ из сети — задайте пароль. Basic auth передаёт пароль открытым текстом, поэтому за пределами домашней сети или VPN ставьте сервис за HTTPS (nginx, Caddy).

## Как сервис systemd (пользовательский)

`platezhi.service` рассчитан на проект в `~/Projects/platezhi`; если он лежит в другом месте, поправьте пути.

```sh
cp .env.example .env            # и отредактируйте
mkdir -p ~/.config/systemd/user
ln -s "$PWD/platezhi.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now platezhi
loginctl enable-linger "$USER"  # чтобы работал без входа в систему
```

```sh
systemctl --user restart platezhi    # после правок кода или .env
journalctl --user -u platezhi -f     # логи
```

В unit-файле стоят лимиты: 64 МБ памяти, 20% CPU.

## Доступ из интернета (nginx + HTTPS)

Приложение слушает только `127.0.0.1`, наружу его отдаёт nginx по HTTPS:

1. В `.env`: `PLATEZHI_HOST=127.0.0.1` и длинный `PLATEZHI_PASSWORD`.
2. DNS-запись домена (например `payments.example.com`) на внешний адрес сервера, проброшены порты 80 и 443.
3. Конфиг nginx по образцу `deploy/nginx.conf.example` (HTTPS, лимит запросов против подбора пароля, заголовки безопасности), затем `certbot --nginx -d payments.example.com --redirect --hsts`.

Порт приложения (8095) в файрволе открывать не нужно.

## API

| | |
|---|---|
| `GET /api/subs` | все платежи (с `ETag`, повторный запрос без изменений → `304`) |
| `PUT /api/subs/<id>` | создать или заменить платёж |
| `DELETE /api/subs/<id>` | удалить |

## Бэкап

```sh
sqlite3 data/platezhi.db ".backup platezhi-backup.db"
```

## Тесты

API и интерфейс в настоящем браузере (headless Firefox через Playwright):

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/playwright install firefox
.venv/bin/pytest tests -q
```

Тесты поднимают отдельный сервер на свободном порту с временной базой, рабочие данные не трогаются.
