import asyncio
import hmac
import json
import os
import time
import urllib.request
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import a2s
from mcstatus import JavaServer

# =====================================================================
# 1. НАСТРОЙКИ ДОСТУПА В АДМИНКУ И DISCORD ВИДЖЕТ
# =====================================================================
ADMIN_USERNAME = os.getenv("IRKGAMES_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("IRKGAMES_ADMIN_PASS", "changeme123")
SECRET_KEY = os.getenv("IRKGAMES_SECRET_KEY", "change-this-secret-key-in-prod")

# Вставьте сюда ID вашего Discord-сервера (из Настройки сервера -> Виджет), чтобы показывать онлайн в голосе
DISCORD_GUILD_ID = ""
DISCORD_INVITE_URL = "https://discord.gg/ZfDzahnXyJ"

DATA_FILE = "/opt/games_portal/data.json"
HISTORY_FILE = "/opt/games_portal/history.json"
POLL_INTERVAL = 20
HISTORY_INTERVAL = 900

# =====================================================================
# 2. СПРАВОЧНИК КАМПАНИЙ L4D2
# =====================================================================
L4D2_CAMPAIGNS = {
    "c1m": ("Вымерший центр", "Dead Center", "amber"),
    "c2m": ("Мрачный карнавал", "Dark Carnival", "fuchsia"),
    "c3m": ("Болотная лихорадка", "Swamp Fever", "emerald"),
    "c4m": ("Ужасный ливень", "Hard Rain", "sky"),
    "c5m": ("Приход", "The Parish", "rose"),
    "c6m": ("Переход", "The Passing", "indigo"),
    "c7m": ("Жертва", "The Sacrifice", "rose"),
    "c8m": ("Нет милосердию", "No Mercy", "sky"),
    "c9m": ("Роковой полёт", "Crash Course", "amber"),
    "c10m": ("Похоронный звон", "Death Toll", "emerald"),
    "c11m": ("Смерть в воздухе", "Dead Air", "indigo"),
    "c12m": ("Кровавая жатва", "Blood Harvest", "rose"),
    "c13m": ("Холодный ручей", "Cold Stream", "cyan"),
    "c14m": ("Последний рубеж", "The Last Stand", "amber"),
}

def parse_l4d2_map(map_code: str) -> dict:
    if not map_code or map_code == "-":
        return {"title": "Ожидание лобби", "chapter": "", "accent": "slate"}
    prefix = map_code[:4].lower() if map_code.lower().startswith(("c10", "c11", "c12", "c13", "c14")) else map_code[:3].lower()
    camp_ru, camp_en, accent = L4D2_CAMPAIGNS.get(prefix, (map_code, "Custom Campaign", "rose"))
    chapter = ""
    if "_" in map_code:
        part = map_code.split("_", 1)[1].replace("_", " ").title()
        chapter = part
    return {"title": camp_ru, "subtitle": camp_en, "chapter": chapter, "accent": accent}

def calc_7dtd_bloodmoon(rules: dict) -> dict:
    try:
        server_time = int(rules.get("CurrentServerTime", 0))
        if server_time <= 0:
            return {}
        # В 7DTD 24000 тиков = 1 игровой день (начинается с Дня 1 в 07:00 -> 7000 тиков)
        day = (server_time // 24000) + 1
        ticks_in_day = server_time % 24000
        hour = (ticks_in_day // 1000) % 24
        minute = int(((ticks_in_day % 1000) / 1000) * 60)
        bm_freq = int(rules.get("BloodMoonFrequency", 7)) or 7

        rem = bm_freq - (day % bm_freq)
        if rem == bm_freq:
            rem = 0

        is_tonight = (rem == 0) or (rem == bm_freq - 1 and hour < 4)
        status_text = "Кровавая Луна СЕГОДНЯ!" if is_tonight else f"До орды: {rem} дн."
        return {
            "day": day,
            "time_str": f"{hour:02d}:{minute:02d}",
            "days_left": rem,
            "is_bloodmoon": is_tonight,
            "status_text": status_text
        }
    except Exception:
        return {}

# =====================================================================
# 3. НАЧАЛЬНЫЕ ДАННЫЕ
# =====================================================================
DEFAULT_DATA = {
    "news": [
        {
            "id": "news-1",
            "date": "26.11.2026",
            "tag": "Запуск",
            "tag_color": "indigo",
            "title": "Обновление портала IrkGames Network",
            "summary": "Добавлены трекер Кровавой Луны для 7DTD, превью кампаний L4D2 и живой мониторинг Discord.",
            "content": "Добро пожаловать на обновлённый портал <b>IrkGames</b>!<br><br>Теперь прямо на карточках серверов отображаются:<br>• Текущий игровой день и отсчёт до <b>Кровавой Луны</b> в 7 Days to Die;<br>• Названия кампаний и глав в Left 4 Dead 2;<br>• Статус голосовых каналов Discord."
        },
        {
            "id": "news-2",
            "date": "24.11.2026",
            "tag": "7 Days to Die",
            "tag_color": "amber",
            "title": "Стабильная работа сервера IrkGames 7DTD",
            "summary": "Сервер переведён на выделенные ресурсы Proxmox. Приглашаем выживших к совместному строительству базы.",
            "content": "Сервер <b>IrkGames — Выживание (7 Days to Die)</b> работает в штатном режиме на порту <code>26900</code>.<br><br>• Сложность: Выживание<br>• Максимум слотов: 8<br>• Подключение: через кнопку <b>«Играть»</b> на карточке сервера или по IP <code>91.237.223.118:26900</code>."
        },
        {
            "id": "news-3",
            "date": "22.11.2026",
            "tag": "Left 4 Dead 2",
            "tag_color": "rose",
            "title": "Доступны два выделенных сервера L4D2",
            "summary": "Подняты два независимых сервера для кооперативного прохождения кампаний и режима Сражения.",
            "content": "Доступны сразу два выделенных сервера <b>Left 4 Dead 2</b>:<br><br>• <b>Сервер #1:</b> <code>91.237.223.118:27015</code><br>• <b>Сервер #2:</b> <code>91.237.223.118:27016</code>"
        }
    ],
    "servers": [
        {
            "id": "mc",
            "category": "minecraft",
            "category_title": "Minecraft",
            "category_desc": "Классическое выживание, строительство и исследование мира",
            "name": "Minecraft — Выживание",
            "game_type": "minecraft",
            "local_ip": "192.168.88.23",
            "query_port": 25565,
            "public_address": "91.237.223.118:25565",
            "connect_type": "copy",
            "modpack_url": "",
            "guide": "1. Запустите клиент Minecraft.<br>2. Откройте «Сетевая игра» → «Добавить сервер».<br>3. Вставьте адрес <code>91.237.223.118:25565</code> и подключайтесь."
        },
        {
            "id": "7dtd",
            "category": "7dtd",
            "category_title": "7 Days to Die",
            "category_desc": "Выживание в постапокалипсисе, крафт и оборона от орды",
            "name": "IrkGames — Выживание",
            "game_type": "a2s",
            "local_ip": "192.168.88.23",
            "query_port": 26900,
            "public_address": "91.237.223.118:26900",
            "connect_type": "steam",
            "modpack_url": "",
            "guide": "1. Если на сервере установлены моды, отключите EAC в лаунчере игры.<br>2. Нажмите «Запустить через Steam» или введите <code>91.237.223.118</code> (порт <code>26900</code>) в игре."
        },
        {
            "id": "l4d2",
            "category": "l4d2",
            "category_title": "Left 4 Dead 2",
            "category_desc": "Кооперативное прохождение кампаний и режим Сражения",
            "name": "Left 4 Dead 2 — Сервер #1",
            "game_type": "a2s",
            "local_ip": "192.168.88.23",
            "query_port": 27015,
            "public_address": "91.237.223.118:27015",
            "connect_type": "steam",
            "modpack_url": "",
            "guide": "1. Нажмите «Запустить через Steam».<br>2. Или откройте консоль (~) в игре и введите: <code>connect 91.237.223.118:27015</code>"
        },
        {
            "id": "l4d2_2",
            "category": "l4d2",
            "category_title": "Left 4 Dead 2",
            "category_desc": "Кооперативное прохождение кампаний и режим Сражения",
            "name": "Left 4 Dead 2 — Сервер #2",
            "game_type": "a2s",
            "local_ip": "192.168.88.23",
            "query_port": 27016,
            "public_address": "91.237.223.118:27016",
            "connect_type": "steam",
            "modpack_url": "",
            "guide": "1. Нажмите «Запустить через Steam».<br>2. Или откройте консоль (~) в игре и введите: <code>connect 91.237.223.118:27016</code>"
        }
    ]
}

serializer = URLSafeTimedSerializer(SECRET_KEY)
server_states = {}
uptime_tracker = {}
history_data = {}
discord_state = {"online": 0, "voice": 0, "members": [], "enabled": False}
last_history_save = 0

def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULT_DATA))

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_authenticated(request: Request) -> bool:
    token = request.cookies.get("admin_session")
    if not token:
        return False
    try:
        payload = serializer.loads(token, max_age=86400 * 7)
        return payload.get("user") == ADMIN_USERNAME
    except (BadSignature, SignatureExpired, Exception):
        return False

def load_history():
    global history_data
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                for sid, points in raw.items():
                    history_data[sid] = deque(points, maxlen=96)
        except Exception:
            pass

def save_history():
    try:
        serializable = {sid: list(dq) for sid, dq in history_data.items()}
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False)
    except Exception:
        pass

def format_duration(seconds: float) -> str:
    sec = int(seconds)
    if sec < 60:
        return "менее минуты"
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}д")
    if hours > 0:
        parts.append(f"{hours}ч")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}м")
    return " ".join(parts)

def fetch_discord_widget_sync():
    if not DISCORD_GUILD_ID:
        return {"online": 0, "voice": 0, "members": [], "enabled": False}
    url = f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/widget.json"
    req = urllib.request.Request(url, headers={"User-Agent": "IrkGamesPortal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            members = payload.get("members", [])
            voice_users = [m.get("username") for m in members if m.get("channel_id")]
            return {
                "online": payload.get("presence_count", len(members)),
                "voice": len(voice_users),
                "members": voice_users[:8],
                "enabled": True
            }
    except Exception:
        return {"online": 0, "voice": 0, "members": [], "enabled": False}

async def poll_single_server(srv: dict, now: float, record_point: bool, time_label: str) -> dict:
    sid = srv["id"]
    tracker = uptime_tracker.setdefault(sid, {"online_since": None, "fails": 0})
    dq = history_data.setdefault(sid, deque(maxlen=96))

    state = {
        **srv,
        "online": False,
        "players": 0,
        "max_players": 0,
        "player_list": [],
        "map": "-",
        "ping_ms": None,
        "uptime": "Офлайн",
        "extra_info": {},
        "history": list(dq)
    }

    try:
        t0 = time.perf_counter()
        if srv["game_type"] == "minecraft":
            mc = await JavaServer.async_lookup(f"{srv['local_ip']}:{srv['query_port']}", timeout=3.0)
            status = await mc.async_status()
            ping = max(1, round((time.perf_counter() - t0) * 1000))

            state["online"] = True
            state["players"] = status.players.online
            state["max_players"] = status.players.max
            state["map"] = status.version.name
            state["ping_ms"] = ping
            state["extra_info"] = {"type": "mc", "motd": "Java Edition"}
            if status.players.sample:
                state["player_list"] = [p.name for p in status.players.sample if p.name]

        elif srv["game_type"] == "a2s":
            addr = (srv["local_ip"], int(srv["query_port"]))
            info = await a2s.ainfo(addr, timeout=3.0)
            ping = max(1, round((time.perf_counter() - t0) * 1000))

            state["online"] = True
            state["players"] = info.player_count
            state["max_players"] = info.max_players
            state["map"] = info.map_name
            state["ping_ms"] = ping

            if srv["category"] == "l4d2":
                state["extra_info"] = {"type": "l4d2", **parse_l4d2_map(info.map_name)}
            elif srv["category"] == "7dtd":
                try:
                    rules = await a2s.arules(addr, timeout=2.5)
                    bm = calc_7dtd_bloodmoon(rules)
                    if bm:
                        state["extra_info"] = {"type": "7dtd", **bm}
                except Exception:
                    pass

            try:
                players = await a2s.aplayers(addr, timeout=2.0)
                state["player_list"] = [p.name for p in players if p.name.strip()]
            except Exception:
                pass

        if state["online"]:
            tracker["fails"] = 0
            if tracker["online_since"] is None:
                tracker["online_since"] = now
            state["uptime"] = format_duration(now - tracker["online_since"])

    except Exception:
        tracker["fails"] += 1
        if tracker["fails"] >= 2:
            tracker["online_since"] = None
            state["online"] = False
            state["uptime"] = "Офлайн"
        elif sid in server_states and server_states[sid]["online"]:
            state = {**server_states[sid], **srv, "history": list(dq)}

    if record_point:
        dq.append({"t": time_label, "p": state["players"] if state["online"] else 0})
        state["history"] = list(dq)

    return state

async def poll_servers():
    global last_history_save, discord_state
    load_history()

    while True:
        data = load_data()
        servers_cfg = data.get("servers", [])
        active_ids = {s["id"] for s in servers_cfg}

        for old_id in list(server_states.keys()):
            if old_id not in active_ids:
                server_states.pop(old_id, None)

        now = time.time()
        record_point = (now - last_history_save) >= HISTORY_INTERVAL
        time_label = datetime.now().strftime("%H:%M")

        for srv in servers_cfg:
            state = await poll_single_server(srv, now, record_point, time_label)
            server_states[srv["id"]] = state

        if record_point:
            last_history_save = now
            save_history()

        await asyncio.sleep(POLL_INTERVAL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_data()
    task = asyncio.create_task(poll_servers())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="/opt/games_portal/templates")

def build_grouped_servers():
    data = load_data()
    servers_cfg = data.get("servers", [])
    current = []
    for s in servers_cfg:
        if s["id"] in server_states:
            merged = {**server_states[s["id"]], **s}
            current.append(merged)
        else:
            current.append({
                **s, "online": False, "players": 0, "max_players": 0,
                "player_list": [], "map": "-", "ping_ms": None,
                "uptime": "Опрос...", "extra_info": {}, "history": []
            })

    groups = {}
    for srv in current:
        cat = srv["category"]
        if cat not in groups:
            groups[cat] = {
                "id": cat,
                "title": srv["category_title"],
                "desc": srv["category_desc"],
                "servers": []
            }
        groups[cat]["servers"].append(srv)
    return list(groups.values()), current, data.get("news", [])

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    groups, all_servers, news = build_grouped_servers()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "groups": groups,
            "servers": all_servers,
            "news": news,
            "discord": discord_state,
            "discord_url": DISCORD_INVITE_URL
        }
    )

@app.get("/api/status")
async def api_status():
    _, all_servers, _ = build_grouped_servers()
    return {"servers": all_servers, "discord": discord_state}

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, msg: str = "", err: str = ""):
    if not is_authenticated(request):
        return templates.TemplateResponse(request=request, name="admin_login.html", context={"err": err})
    data = load_data()
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"servers": data.get("servers", []), "news": data.get("news", []), "msg": msg, "err": err}
    )

@app.post("/admin/login")
async def admin_login(username: str = Form(...), password: str = Form(...)):
    user_ok = hmac.compare_digest(username.strip(), ADMIN_USERNAME)
    pass_ok = hmac.compare_digest(password, ADMIN_PASSWORD)
    if user_ok and pass_ok:
        token = serializer.dumps({"user": ADMIN_USERNAME})
        resp = RedirectResponse(url="/admin?msg=ok", status_code=303)
        resp.set_cookie("admin_session", token, httponly=True, max_age=86400 * 7, samesite="lax")
        return resp
    return RedirectResponse(url="/admin?err=Invalid+login+or+password", status_code=303)

@app.get("/admin/logout")
async def admin_logout():
    resp = RedirectResponse(url="/admin", status_code=303)
    resp.delete_cookie("admin_session")
    return resp

@app.post("/admin/server/save")
async def save_server(
    request: Request,
    server_id: str = Form(""),
    category: str = Form(...),
    category_title: str = Form(...),
    category_desc: str = Form(""),
    name: str = Form(...),
    game_type: str = Form(...),
    local_ip: str = Form(...),
    query_port: int = Form(...),
    public_address: str = Form(...),
    connect_type: str = Form(...),
    modpack_url: str = Form(""),
    guide: str = Form("")
):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)

    data = load_data()
    servers = data.setdefault("servers", [])

    srv_obj = {
        "id": server_id.strip() or f"srv_{uuid.uuid4().hex[:6]}",
        "category": category.strip().lower(),
        "category_title": category_title.strip(),
        "category_desc": category_desc.strip(),
        "name": name.strip(),
        "game_type": game_type.strip(),
        "local_ip": local_ip.strip(),
        "query_port": int(query_port),
        "public_address": public_address.strip(),
        "connect_type": connect_type.strip(),
        "modpack_url": modpack_url.strip(),
        "guide": guide.strip()
    }

    updated = False
    for i, s in enumerate(servers):
        if s["id"] == srv_obj["id"]:
            servers[i] = srv_obj
            updated = True
            break
    if not updated:
        servers.append(srv_obj)

    save_data(data)
    state = await poll_single_server(srv_obj, time.time(), False, datetime.now().strftime("%H:%M"))
    server_states[srv_obj["id"]] = state
    return RedirectResponse(url="/admin?msg=Server+saved", status_code=303)

@app.post("/admin/server/delete")
async def delete_server(request: Request, server_id: str = Form(...)):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    data = load_data()
    data["servers"] = [s for s in data.get("servers", []) if s["id"] != server_id]
    save_data(data)
    server_states.pop(server_id, None)
    return RedirectResponse(url="/admin?msg=Server+deleted", status_code=303)

@app.post("/admin/news/save")
async def save_news(
    request: Request,
    news_id: str = Form(""),
    date: str = Form(""),
    tag: str = Form(...),
    tag_color: str = Form("indigo"),
    title: str = Form(...),
    summary: str = Form(...),
    content: str = Form(...)
):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    data = load_data()
    news_list = data.setdefault("news", [])

    news_obj = {
        "id": news_id.strip() or f"news_{uuid.uuid4().hex[:6]}",
        "date": date.strip() or datetime.now().strftime("%d.%m.%Y"),
        "tag": tag.strip(),
        "tag_color": tag_color.strip(),
        "title": title.strip(),
        "summary": summary.strip(),
        "content": content.strip()
    }

    updated = False
    for i, n in enumerate(news_list):
        if n["id"] == news_obj["id"]:
            news_list[i] = news_obj
            updated = True
            break
    if not updated:
        news_list.insert(0, news_obj)

    save_data(data)
    return RedirectResponse(url="/admin?msg=News+saved", status_code=303)

@app.post("/admin/news/delete")
async def delete_news(request: Request, news_id: str = Form(...)):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    data = load_data()
    data["news"] = [n for n in data.get("news", []) if n["id"] != news_id]
    save_data(data)
    return RedirectResponse(url="/admin?msg=News+deleted", status_code=303)
