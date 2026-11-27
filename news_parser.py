import json
import re
import time
import urllib.request
from datetime import datetime

CACHE_TTL = 1800  # 30 минут
_cache = {"timestamp": 0, "items": []}

STEAM_GAMES = [
    {"appid": 251570, "game": "7 Days to Die", "tag": "7dtd", "color": "red"},
    {"appid": 550, "game": "Left 4 Dead 2", "tag": "l4d2", "color": "emerald"},
]

def _clean_html(text: str, max_len: int = 220) -> str:
    text = re.sub(r"\[/?[a-zA-Z0-9*=_]+\]", " ", text)  # BBCode Steam
    text = re.sub(r"<[^>]+>", " ", text)               # HTML
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:max_len] + "...") if len(text) > max_len else text

def _fetch_steam_news() -> list:
    results = []
    for g in STEAM_GAMES:
        url = (
            f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
            f"?appid={g['appid']}&count=4&maxlength=300&feeds=steam_community_announcements"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "IrkGamesPortal/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("appnews", {}).get("newsitems", []):
                ts = int(item.get("date", 0))
                results.append({
                    "game": g["game"],
                    "tag": g["tag"],
                    "color": g["color"],
                    "title": item.get("title", "Обновление"),
                    "summary": _clean_html(item.get("contents", "")),
                    "url": item.get("url", ""),
                    "timestamp": ts,
                    "date": datetime.fromtimestamp(ts).strftime("%d.%m.%Y") if ts else "",
                })
        except Exception:
            continue
    return results

def _fetch_minecraft_news() -> list:
    url = "https://launchercontent.mojang.com/news.json"
    results = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "IrkGamesPortal/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        entries = [e for e in data.get("entries", []) if e.get("category") in ("Minecraft: Java Edition", "News")][:4]
        for item in entries:
            date_str = item.get("date", "")
            ts = 0
            try:
                ts = int(datetime.strptime(date_str[:10], "%Y-%m-%d").timestamp())
            except Exception:
                pass
            read_more = item.get("readMoreLink", "https://www.minecraft.net/en-us/articles")
            if read_more.startswith("/"):
                read_more = "https://www.minecraft.net" + read_more
            results.append({
                "game": "Minecraft",
                "tag": "minecraft",
                "color": "amber",
                "title": item.get("title", "Minecraft Update"),
                "summary": _clean_html(item.get("text", "")),
                "url": read_more,
                "timestamp": ts,
                "date": datetime.fromtimestamp(ts).strftime("%d.%m.%Y") if ts else date_str[:10],
            })
    except Exception:
        pass
    return results

def get_game_news(force: bool = False) -> list:
    now = time.time()
    if not force and _cache["items"] and (now - _cache["timestamp"] < CACHE_TTL):
        return _cache["items"]

    items = _fetch_steam_news() + _fetch_minecraft_news()
    items.sort(key=lambda x: x["timestamp"], reverse=True)
    if items:
        _cache["items"] = items[:12]
        _cache["timestamp"] = now
    return _cache["items"]
