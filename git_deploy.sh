#!/usr/bin/env bash
set -e
cd /opt/games_portal
MSG="${1:-Обновление портала IrkGames}"
DATE_STR=$(date "+%Y-%m-%d %H:%M")
[ -z "$(git status -s)" ] && echo "Нет изменений для отправки." && exit 0
touch CHANGELOG.md
{ echo "### ${DATE_STR} — ${MSG}"; git status -s | awk '{print "- `" $2 "` (" $1 ")"}'; echo ""; cat CHANGELOG.md; } > /tmp/chg.tmp && mv /tmp/chg.tmp CHANGELOG.md
git add -A
git commit -m "${MSG} (${DATE_STR})"
git push origin main
echo "Готово! Изменения и CHANGELOG.md отправлены на GitHub."
