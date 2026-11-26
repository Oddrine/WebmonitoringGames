#!/usr/bin/env bash
set -e
[ "$EUID" -ne 0 ] && echo "Запустите от имени root" && exit 1

read -rp "Логин админа [Oddine]: " ADMIN_USER
ADMIN_USER=${ADMIN_USER:-Oddine}
read -rsp "Пароль админа [3212AA3212AA]: " ADMIN_PASS
echo ""
ADMIN_PASS=${ADMIN_PASS:-3212AA3212AA}
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))' 2>/dev/null || echo "irk-$(date +%s)")

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl wget ca-certificates python3 python3-venv python3-pip nginx

if [ -d "/opt/games_portal/.git" ]; then
    cd /opt/games_portal && git fetch origin main && git reset --hard origin/main
else
    rm -rf /opt/games_portal && git clone [https://github.com/Oddrine/WebmonitoringGames.git](https://github.com/Oddrine/WebmonitoringGames.git) /opt/games_portal && cd /opt/games_portal
fi

python3 -m venv /opt/games_portal/venv
/opt/games_portal/venv/bin/pip install -q -r /opt/games_portal/requirements.txt

printf "[Unit]\nDescription=IrkGames\nAfter=network.target\n[Service]\nUser=root\nWorkingDirectory=/opt/games_portal\nExecStart=/opt/games_portal/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000\nRestart=always\n[Install]\nWantedBy=multi-user.target\n" > /etc/systemd/system/games-portal.service
mkdir -p /etc/systemd/system/games-portal.service.d
printf "[Service]\nEnvironment=\"IRKGAMES_ADMIN_USER=%s\"\nEnvironment=\"IRKGAMES_ADMIN_PASS=%s\"\nEnvironment=\"IRKGAMES_SECRET_KEY=%s\"\n" "$ADMIN_USER" "$ADMIN_PASS" "$SECRET_KEY" > /etc/systemd/system/games-portal.service.d/override.conf

printf "server {\n    listen 80 default_server;\n    server_name _;\n    location / {\n        proxy_pass [http://127.0.0.1:8000](http://127.0.0.1:8000);\n        proxy_set_header Host \$host;\n        proxy_set_header X-Real-IP \$remote_addr;\n    }\n}\n" > /etc/nginx/sites-available/games-portal
rm -f /etc/nginx/sites-enabled/default && ln -sf /etc/nginx/sites-available/games-portal /etc/nginx/sites-enabled/games-portal
systemctl daemon-reload && systemctl enable --now games-portal nginx && systemctl restart games-portal nginx
echo "Готово! Сайт: http://$(hostname -I | awk '{print $1}')/"
