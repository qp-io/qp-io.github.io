#!/bin/bash
set -e
declare -A defaults
declare -A config_file
declare -A args
declare -A config
declare -A users
declare -A path
declare -A service
declare -A md5
declare -A regex
declare -A image

config_path="${REALITY_CONFIG_PATH:-/opt/reality-ezpz}"
instance_name="$(basename "${config_path}")"
compose_project="${instance_name}"
tgbot_project="tgbot-${instance_name}"

_sn_idx=$(printf '%s' "${instance_name}" | cksum | awk '{print ($1 % 3800) + 100}')
_sn_main=$(( _sn_idx * 2 ))
_sn_tgbot=$(( _sn_idx * 2 + 1 ))
subnet_main="fc12::$(printf '%x' ${_sn_main}):0/112"
subnet_tgbot="fc12::$(printf '%x' ${_sn_tgbot}):0/112"

BACKTITLE="Панель управления RealityEZPZ [${instance_name}]"
MENU="Выберите действие:"
HEIGHT=30
WIDTH=65
CHOICE_HEIGHT=20

image[xray]="teddysun/xray:latest"
image[nginx]="nginx:latest"
image[certbot]="certbot/certbot:latest"
image[haproxy]="haproxy:latest"
image[python]="python:3.12-alpine"
image[wgcf]="virb3/wgcf:latest"

# === DEFAULTS ===
defaults[protocol]=vless
defaults[transport]=tcp
defaults[domain]=yahoo.com
defaults[port]=443
defaults[safenet]=OFF
defaults[warp]=OFF
defaults[warp_license]=""
defaults[warp_private_key]=""
defaults[warp_token]=""
defaults[warp_id]=""
defaults[warp_client_id]=""
defaults[warp_interface_ipv4]=""defaults[warp_interface_ipv6]=""
defaults[security]=reality
defaults[server]=$(curl -fsSL --ipv4 https://cloudflare.com/cdn-cgi/trace | grep ip | cut -d '=' -f2)
defaults[tgbot]=OFF
defaults[tgbot_token]=""
defaults[tgbot_admins]=""
defaults[host_header]=""

config_items=(
"protocol"
"security"
"service_path"
"host_header"
"public_key"
"private_key"
"short_id"
"transport"
"domain"
"server"
"port"
"safenet"
"warp"
"warp_license"
"warp_private_key"
"warp_token"
"warp_id"
"warp_client_id"
"warp_interface_ipv4"
"warp_interface_ipv6"
"tgbot"
"tgbot_token"
"tgbot_admins"
)

regex[domain]="^[a-zA-Z0-9]+([-.][a-zA-Z0-9]+)*\.[a-zA-Z]{2,}$"
regex[port]="^[1-9][0-9]*$"
regex[warp_license]="^[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}$"
regex[username]="^[a-zA-Z0-9]+$"
regex[ip]="^([0-9]{1,3}\.){3}[0-9]{1,3}$"
regex[tgbot_token]="^[0-9]{8,10}:[a-zA-Z0-9_-]{35}$"
regex[tgbot_admins]="^[a-zA-Z][a-zA-Z0-9_]{4,31}(,[a-zA-Z][a-zA-Z0-9_]{4,31})*$"
regex[domain_port]="^[a-zA-Z0-9]+([-.][a-zA-Z0-9]+)*\.[a-zA-Z]{2,}(:[1-9][0-9]*)?$"
regex[file_path]="^[a-zA-Z0-9_/.-]+$"
regex[url]="^(http|https)://([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|[0-9]{1,3}(\.[0-9]{1,3}){3})(:[0-9]{1,5})?(/.*)?$"
regex[path]="^/.*$"
regex[protocol]="^(vless|hysteria2)$"

function show_help {
echo ""
echo "Использование: reality-ezpz.sh [--protocol=vless|hysteria2] [-t|--transport=tcp|http|xhttp|grpc|ws] [-d|--domain=<домен>] [--server=<сервер>] [--regenerate] [--default][-r|--restart] [--enable-safenet=true|false] [--port=<порт>] [--enable-warp=true|false]
[--warp-license=<лицензия>] [--security=reality|letsencrypt|selfsigned|notls] [-m|--menu] [--show-server-config] [--add-user=<имя>] [--lists-users]
[--show-user=<имя>] [--delete-user=<имя>] [--backup] [--restore=<url|файл>] [--backup-password=<пароль>] [-u|--uninstall]
[--path=<путь>] [--host=<хост>]"
echo ""
echo "      --protocol <протокол>    Протокол: vless или hysteria2 (по умолчанию: ${defaults[protocol]})"
echo "  -t, --transport <протокол>   Транспорт (только для vless: tcp|http|xhttp|grpc|ws)"
echo "  -d, --domain <домен>         Домен для SNI (по умолчанию: ${defaults[domain]})"
echo "      --server <сервер>        IP или домен сервера"
echo "      --path <путь>            Путь сервиса (для ws, grpc, xhttp, http — только vless)"
echo "      --host <хост>            Заголовок Host (для ws, http, xhttp — только vless)"
echo "      --regenerate             Перегенерировать ключи"
echo "      --default                Сбросить настройки"
echo "  -r  --restart                Перезапустить службы"
echo "  -u, --uninstall              Удалить"
echo "      --enable-safenet <bool>  Блокировка вредоносного контента"
echo "      --port <порт>            Порт (по умолчанию: ${defaults[port]})"
echo "      --enable-warp <bool>     Включить Cloudflare WARP"
echo "      --warp-license <лиц>     Лицензия WARP+"
echo "      --security <тип>         Шифрование: reality|letsencrypt|selfsigned|notls (notls только для vless)"
echo "  -m  --menu                   Меню"
echo "      --enable-tgbot <bool>    Включить Telegram-бота"
echo "      --tgbot-token <токен>    Токен бота"
echo "      --tgbot-admins <юзеры>   Админы бота (через запятую, без @)"
echo "      --show-server-config     Показать конфиг сервера"
echo "      --add-user <имя>         Добавить пользователя"
echo "      --list-users             Список пользователей"
echo "      --show-user <имя>        Показать конфиг пользователя"
echo "      --delete-user <имя>      Удалить пользователя"
echo "      --backup                 Создать бэкап"
echo "      --restore <url|файл>     Восстановить из бэкапа"
echo "      --backup-password <пароль> Пароль для бэкапа"
echo "  -h, --help                   Помощь"
return 1
}

function parse_args {
local opts
opts=$(getopt -o t:d:ruc:mh --long protocol:,transport:,domain:,server:,path:,host:,regenerate,default,restart,uninstall,enable-safenet:,port:,warp-license:,enable-warp:,security:,menu,show-server-config,add-user:,list-users,show-user:,delete-user:,backup,restore:,backup-password:,enable-tgbot:,tgbot-token:,tgbot-admins:,help -- "$@")
if [[ $? -ne 0 ]]; then return 1; fi
eval set -- "$opts"
while true; do
case $1 in
--protocol)
args[protocol]="$2"
case ${args[protocol]} in vless|hysteria2) shift 2 ;; *) echo "Неверный протокол: ${args[protocol]}"; return 1 ;; esac ;;
-t|--transport)
args[transport]="$2"
case ${args[transport]} in tcp|http|xhttp|grpc|ws) shift 2 ;; *) echo "Неверный транспорт: ${args[transport]}"; return 1 ;; esac ;;
-d|--domain)args[domain]="$2"
[[ ${args[domain]} =~ ${regex[domain_port]} ]] || { echo "Неверный домен"; return 1; }
shift 2 ;;
--server)
args[server]="$2"
[[ ${args[server]} =~ ${regex[domain]} || ${args[server]} =~ ${regex[ip]} ]] || { echo "Неверный сервер"; return 1; }
shift 2 ;;
--path)
args[service_path]="${2#/}"
shift 2 ;;
--host)
args[host_header]="$2"
shift 2 ;;
--regenerate) args[regenerate]=true; shift ;;
--default) args[default]=true; shift ;;
-r|--restart) args[restart]=true; shift ;;
-u|--uninstall) args[uninstall]=true; shift ;;
--enable-safenet)
case "$2" in true|false) $2 && args[safenet]=ON || args[safenet]=OFF; shift 2 ;; *) echo "Неверный safenet"; return 1 ;; esac ;;
--enable-warp)
case "$2" in true|false) $2 && args[warp]=ON || args[warp]=OFF; shift 2 ;; *) echo "Неверный warp"; return 1 ;; esac ;;
--port)
args[port]="$2"
[[ ${args[port]} =~ ${regex[port]} && ${args[port]} -ge 1 && ${args[port]} -le 65535 ]] || { echo "Неверный порт"; return 1; }
shift 2 ;;
--warp-license)
args[warp_license]="$2"
[[ ${args[warp_license]} =~ ${regex[warp_license]} ]] || { echo "Неверная лицензия"; return 1; }
shift 2 ;;
--security)
args[security]="$2"
case ${args[security]} in reality|letsencrypt|selfsigned|notls) shift 2 ;; *) echo "Неверная безопасность"; return 1 ;; esac ;;
-m|--menu) args[menu]=true; shift ;;
--enable-tgbot)
case "$2" in true|false) $2 && args[tgbot]=ON || args[tgbot]=OFF; shift 2 ;; *) echo "Неверный tgbot"; return 1 ;; esac ;;
--tgbot-token)
args[tgbot_token]="$2"
[[ ${args[tgbot_token]} =~ ${regex[tgbot_token]} ]] || { echo "Неверный токен"; return 1; }
curl -sSfL -m 3 "https://api.telegram.org/bot${args[tgbot_token]}/getMe" >/dev/null 2>&1 || { echo "Токен недействителен"; return 1; }
shift 2 ;;
--tgbot-admins)
args[tgbot_admins]="$2"
[[ ${args[tgbot_admins]} =~ ${regex[tgbot_admins]} ]] || { echo "Неверные админы"; return 1; }
shift 2 ;;
--show-server-config) args[server-config]=true; shift ;;
--add-user)
args[add_user]="$2"
[[ ${args[add_user]} =~ ${regex[username]} ]] || { echo "Неверное имя пользователя"; return 1; }
shift 2 ;;
--list-users) args[list_users]=true; shift ;;--show-user) args[show_config]="$2"; shift 2 ;;
--delete-user) args[delete_user]="$2"; shift 2 ;;
--backup) args[backup]=true; shift ;;
--restore)
args[restore]="$2"
[[ ${args[restore]} =~ ${regex[file_path]} || ${args[restore]} =~ ${regex[url]} ]] || { echo "Неверный путь бэкапа"; return 1; }
shift 2 ;;
--backup-password) args[backup_password]="$2"; shift 2 ;;
-h|--help) return 1 ;;
--) shift; break ;;
*) echo "Неизвестная опция: $1"; return 1 ;;
esac
done
[[ ${args[uninstall]} == true ]] && uninstall
[[ -n ${args[warp_license]} ]] && args[warp]=ON
}

function backup {
local backup_name backup_password="$1" backup_file_url
backup_name="reality-ezpz-backup-$(date +%Y-%m-%d_%H-%M-%S).zip"
cd "${config_path}"
[[ -z "${backup_password}" ]] && zip -r "/tmp/${backup_name}" . >/dev/null || zip -P "${backup_password}" -r "/tmp/${backup_name}" . >/dev/null
backup_file_url=$(curl -fsS -m 30 -F "file=@/tmp/${backup_name}" "https://temp.sh/upload") || { rm -f "/tmp/${backup_name}"; echo "Ошибка загрузки бэкапа" >&2; return 1; }
rm -f "/tmp/${backup_name}"
echo "${backup_file_url}"
}

function restore {
local backup_file="$1" backup_password="$2" temp_file unzip_output unzip_exit_code current_state
[[ ! -r ${backup_file} ]] && {
temp_file=$(mktemp -u)
[[ "${backup_file}" =~ ^https?://temp\.sh/ ]] && curl -fSsL -m 30 -X POST "${backup_file}" -o "${temp_file}" || curl -fSsL -m 30 "${backup_file}" -o "${temp_file}"
backup_file="${temp_file}"
}
current_state=$(set +o); set +e
[[ -z "${backup_password}" ]] && unzip_output=$(unzip -P "" -t "${backup_file}" 2>&1) || unzip_output=$(unzip -P "${backup_password}" -t "${backup_file}" 2>&1)
unzip_exit_code=$?; eval "$current_state"
[[ ${unzip_exit_code} -ne 0 || ! $(echo "${unzip_output}" | grep -q 'config'; echo $?) -eq 0 ]] && { echo "Неверный файл бэкапа" >&2; rm -f "${temp_file}"; return 1; }
rm -rf "${config_path}"; mkdir -p "${config_path}"
set +e
[[ -z "${backup_password}" ]] && unzip_output=$(unzip -d "${config_path}" "${backup_file}" 2>&1) || unzip_output=$(unzip -P "${backup_password}" -d "${config_path}" "${backup_file}" 2>&1)
unzip_exit_code=$?; eval "$current_state"
[[ ${unzip_exit_code} -ne 0 ]] && { echo "Ошибка восстановления: ${unzip_output}" >&2; rm -f "${temp_file}"; return 1; }
rm -f "${temp_file}"
}

function dict_expander { local -n dict=$1; for key in "${!dict[@]}"; do echo "${key} ${dict[$key]}"; done; }

function parse_config_file {
[[ ! -r "${path[config]}" ]] && { generate_keys; return 0; }while IFS= read -r line; do
[[ "${line}" =~ ^\s*# || "${line}" =~ ^\s*$ ]] && continue
key=$(echo "$line" | cut -d "=" -f 1); value=$(echo "$line" | cut -d "=" -f 2-)
config_file["${key}"]="${value}"
done < "${path[config]}"
[[ -z "${config_file[public_key]}" || -z "${config_file[private_key]}" || -z "${config_file[short_id]}" ]] && generate_keys
}

function parse_users_file {
mkdir -p "$config_path"; touch "${path[users]}"
while read -r line; do
[[ "${line}" =~ ^\s*# || "${line}" =~ ^\s*$ ]] && continue
IFS="=" read -r key value <<< "${line}"; users["${key}"]="${value}"
done < "${path[users]}"
if [[ -n ${args[add_user]} ]]; then
[[ -z "${users["${args[add_user]}"]}" ]] && users["${args[add_user]}"]=$(cat /proc/sys/kernel/random/uuid) || { echo "Пользователь уже существует"; }
fi
if [[ -n ${args[delete_user]} ]]; then
[[ -n "${users["${args[delete_user]}"]}" ]] && {
[[ ${#users[@]} -eq 1 ]] && { echo "Нельзя удалить единственного пользователя"; exit 1; }; unset users["${args[delete_user]}"]
} || { echo "Пользователь не существует"; exit 1; }
fi
[[ ${#users[@]} -eq 0 ]] && { users[RealityEZPZ]=$(cat /proc/sys/kernel/random/uuid); echo "RealityEZPZ=${users[RealityEZPZ]}" >> "${path[users]}"; return 0; }
}

function restore_defaults {
local defaults_items=("${!defaults[@]}") keep=false
local exclude_list=("warp_license" "tgbot_token")
[[ -n ${config[warp_id]} && -n ${config[warp_token]} ]] && warp_delete_account "${config[warp_id]}" "${config[warp_token]}"
for item in "${defaults_items[@]}"; do
keep=false; for i in "${exclude_list[@]}"; do [[ "${i}" == "${item}" ]] && { keep=true; break; }; done
[[ ${keep} == true ]] && continue; config["${item}"]="${defaults[${item}]}"
done
}

function build_config {
[[ ${args[regenerate]} == true ]] && generate_keys
for item in "${config_items[@]}"; do
[[ -n ${args["${item}"]} ]] && config["${item}"]="${args[${item}]}" ||
[[ -n ${config_file["${item}"]} ]] && config["${item}"]="${config_file[${item}]}" ||
config["${item}"]="${defaults[${item}]}"
done
[[ ${args[default]} == true ]] && { restore_defaults; return 0; }

# Валидации
[[ ${config[protocol]} == 'hysteria2' && ${config[security]} == 'notls' ]] && { echo "Hysteria2 требует TLS"; exit 1; }
[[ ${config[protocol]} == 'hysteria2' && ${config[transport]} != 'tcp' && ${config[transport]} != '' ]] && { echo "Hysteria2 не поддерживает транспорты"; exit 1; }
[[ ${config[protocol]} == 'vless' && ${config[transport]} == 'ws' && ${config[security]} == 'reality' ]] && { echo "WS + Reality несовместимы"; exit 1; }
[[ ${config[protocol]} == 'vless' && ${config[security]} == 'letsencrypt' && ${config[port]} -ne 443 ]] && {
lsof -i :80 >/dev/null 2>&1 || { echo "Порт 80 должен быть свободен для letsencrypt"; exit 1; }}
[[ ${config[security]} == 'letsencrypt' && ! ${config[server]} =~ ${regex[domain]} ]] && { echo "Для letsencrypt нужен домен в --server"; exit 1; }

# WARP логика
if [[ -n ${args[warp]} && "${args[warp]}" == 'OFF' && "${config_file[warp]}" == 'ON' ]]; then
[[ -n ${config[warp_id]} && -n ${config[warp_token]} ]] && warp_delete_account "${config[warp_id]}" "${config[warp_token]}"
fi
if { [[ -n "${args[warp]}" && "${args[warp]}" == 'ON' && "${config_file[warp]}" == 'OFF' ]] || \
[[ "${config[warp]}" == 'ON' && ( -z ${config[warp_private_key]} || -z ${config[warp_token]} || -z ${config[warp_id]} || -z ${config[warp_client_id]} || -z ${config[warp_interface_ipv4]} || -z ${config[warp_interface_ipv6]} ) ]]; }; then
config[warp]='OFF'; warp_create_account || exit 1
[[ -n "${config[warp_license]}" ]] && warp_add_license "${config[warp_id]}" "${config[warp_token]}" "${config[warp_license]}" || exit 1
config[warp]='ON'
fi
[[ -n ${args[warp_license]} && -n ${config_file[warp_license]} && "${args[warp_license]}" != "${config_file[warp_license]}" ]] && {
warp_add_license "${config[warp_id]}" "${config[warp_token]}" "${args[warp_license]}" || { config[warp]='OFF'; config[warp_license]=""; warp_delete_account "${config[warp_id]}" "${config[warp_token]}"; echo "WARP отключён из-за ошибки лицензии"; }
}
}

function update_config_file {
mkdir -p "${config_path}"; touch "${path[config]}"
for item in "${config_items[@]}"; do
grep -q "^${item}=" "${path[config]}" && sed -i "s|^${item}=.*|${item}=${config[${item}]}|" "${path[config]}" || echo "${item}=${config[${item}]}" >> "${path[config]}"
done
check_reload
}

function update_users_file {
rm -f "${path[users]}"
for user in "${!users[@]}"; do echo "${user}=${users[${user}]}" >> "${path[users]}"; done
check_reload
}

function generate_keys {
local key_pair
key_pair=$(docker run --rm ${image[xray]} xray x25519)
config_file[private_key]=$(echo "${key_pair}" | grep 'PrivateKey:' | awk '{print $2}')
config_file[public_key]=$(echo "${key_pair}" | grep -E 'Password' | awk '{print $NF}')
config_file[short_id]=$(openssl rand -hex 8)
config_file[service_path]=$(openssl rand -hex 4)
}

function uninstall {
docker compose --project-directory "${config_path}" down --timeout 2 2>/dev/null || true
docker compose --project-directory "${config_path}/tgbot" -p ${tgbot_project} down --timeout 2 2>/dev/null || true
rm -rf "${config_path}"
echo "Reality-EZPZ удалён."
exit 0
}

function install_packages {[[ -n $BOT_TOKEN ]] && return 0
which qrencode whiptail jq xxd zip unzip >/dev/null 2>&1 && return 0
if which apt >/dev/null 2>&1; then
apt update; DEBIAN_FRONTEND=noninteractive apt install qrencode whiptail jq xxd zip unzip -y; return 0
fi
if which yum >/dev/null 2>&1; then
yum makecache; yum install epel-release -y 2>/dev/null || true; yum install qrencode newt jq vim-common zip unzip -y; return 0
fi
echo "ОС не поддерживается!"; return 1
}

function install_docker {
which docker >/dev/null 2>&1 || { curl -fsSL -m 5 https://get.docker.com | bash; command -v systemctl >/dev/null 2>&1 && systemctl is-system-running >/dev/null 2>&1 && systemctl enable --now docker 2>/dev/null || true; }
docker info >/dev/null 2>&1 || { command -v systemctl >/dev/null 2>&1 && systemctl start docker 2>/dev/null || true; sleep 2; }
docker compose >/dev/null 2>&1 && { docker_cmd="docker compose"; return 0; }
which docker-compose >/dev/null 2>&1 && { docker_cmd="docker-compose"; return 0; }
curl -fsSL -m 30 https://github.com/docker/compose/releases/download/v2.39.1/docker-compose-linux-$(uname -m) -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose; docker_cmd="docker-compose"
}

function generate_docker_compose {
cat >"${path[compose]}" <<EOF
networks:
reality:
driver: bridge
enable_ipv6: true
ipam:
config:
- subnet: ${subnet_main}
services:
engine:
image: ${image[xray]}
restart: always
environment:
TZ: Etc/UTC
volumes:
- ./${path[engine]#${config_path}/}:/etc/xray/config.json
EOF

# Порты в зависимости от протокола
if [[ ${config[protocol]} == 'hysteria2' ]]; then
# Hysteria2: только UDP, TLS обязателен
echo "ports:" >> "${path[compose]}"
echo "- ${config[port]}:${config[port]}/udp" >> "${path[compose]}"
[[ ${config[security]} != 'notls' ]] && {
echo "- ./${path[server_crt]#${config_path}/}:/etc/xray/server.crt" >> "${path[compose]}"
echo "- ./${path[server_key]#${config_path}/}:/etc/xray/server.key" >> "${path[compose]}"
}
else
# VLESS: TCP + опционально 80 для reality/letsencryptecho "ports:" >> "${path[compose]}"
[[ ${config[security]} == 'reality' && ${config[port]} -eq 443 ]] && echo "- 80:8080" >> "${path[compose]}"
echo "- ${config[port]}:8443" >> "${path[compose]}"
[[ ${config[security]} != 'reality' && ${config[security]} != 'notls' ]] && {
echo "- ./${path[server_crt]#${config_path}/}:/etc/xray/server.crt" >> "${path[compose]}"
echo "- ./${path[server_key]#${config_path}/}:/etc/xray/server.key" >> "${path[compose]}"
}
fi

echo "networks:" >> "${path[compose]}"
echo "- reality" >> "${path[compose]}"

# HAProxy + Nginx только для VLESS с TLS/letsencrypt
if [[ ${config[protocol]} == 'vless' && ${config[security]} != 'reality' && ${config[security]} != 'notls' ]]; then
cat >>"${path[compose]}" <<EOF

nginx:
image: ${image[nginx]}
expose:
- 80
restart: always
volumes:
- ./website:/usr/share/nginx/html
networks:
- reality
haproxy:
image: ${image[haproxy]}
ports:
$([[ ${config[security]} == 'letsencrypt' || ${config[port]} -eq 443 ]] && echo '- 80:8080' || true)
- ${config[port]}:8443
restart: always
volumes:
- ./${path[haproxy]#${config_path}/}:/usr/local/etc/haproxy/haproxy.cfg
- ./${path[server_pem]#${config_path}/}:/usr/local/etc/haproxy/server.pem
networks:
- reality
EOF
fi

# Certbot для letsencrypt (только VLESS)
if [[ ${config[protocol]} == 'vless' && ${config[security]} == 'letsencrypt' ]]; then
cat >>"${path[compose]}" <<EOF

certbot:
build:
context: ./certbot
expose:
- 80
restart: always
volumes:- /var/run/docker.sock:/var/run/docker.sock
- ./certbot/data:/etc/letsencrypt
- ./$(dirname "${path[server_pem]#${config_path}/}"):/certificate
- ./${path[certbot_deployhook]#${config_path}/}:/deployhook.sh
- ./${path[certbot_startup]#${config_path}/}:/startup.sh
- ./website:/website
networks:
- reality
entrypoint: /bin/sh
command: /startup.sh
EOF
fi

# Telegram bot
if [[ ${config[tgbot]} == "ON" ]]; then
mkdir -p "${config_path}/tgbot"
generate_tgbot_compose
generate_tgbot_dockerfile
download_tgbot_script
fi
}

function generate_tgbot_compose {
cat >"${path[tgbot_compose]}" <<EOF
networks:
tgbot:
driver: bridge
enable_ipv6: true
ipam:
config:
- subnet: ${subnet_tgbot}
services:
tgbot:
build: ./
restart: always
environment:
BOT_TOKEN: ${config[tgbot_token]}
BOT_ADMIN: ${config[tgbot_admins]}
volumes:
- /var/run/docker.sock:/var/run/docker.sock
- ..:/opt/reality-ezpz
- /etc/docker/:/etc/docker/
networks:
- tgbot
EOF
}

function generate_haproxy_config {
cat > "${path[haproxy]}" <<EOF
globalssl-default-bind-options ssl-min-ver TLSv1.2
defaults
option http-server-close
timeout connect 5s
timeout client 50s
timeout client-fin 1s
timeout server-fin 1s
timeout server 50s
timeout tunnel 50s
timeout http-keep-alive 1s
timeout queue 15s
frontend http
mode http
bind :::8080 v4v6
$(if [[ ${config[security]} == 'letsencrypt' ]]; then echo "
use_backend certbot if { path_beg /.well-known/acme-challenge }
acl letsencrypt-acl path_beg /.well-known/acme-challenge
redirect scheme https if !letsencrypt-acl
"; fi)
use_backend default
frontend tls
bind :::8443 v4v6 ssl crt /usr/local/etc/haproxy/server.pem alpn h2,http/1.1
mode http
http-request set-header Host ${config[server]}
$(if [[ ${config[security]} == 'letsencrypt' ]]; then echo "
use_backend certbot if { path_beg /.well-known/acme-challenge }
"; fi)
use_backend engine if { path_beg /${config[service_path]} }
use_backend default
backend engine
retry-on conn-failure empty-response response-timeout
mode http
$(if [[ ${config[transport]} == 'grpc' ]]; then echo "
server engine engine:8443 check tfo proto h2
"; elif [[ ${config[transport]} == 'http' ]]; then echo "
server engine engine:8443 check tfo ssl verify none
"; else echo "
server engine engine:8443 check tfo
"; fi)
$(if [[ ${config[security]} == 'letsencrypt' ]]; then echo "
backend certbot
mode http
server certbot certbot:80
"; fi)
backend default
mode http
server nginx nginx:80
EOF
}
function generate_certbot_script {
cat >"${path[certbot_startup]}" <<EOF
#!/bin/sh
trap exit TERM
fullchain_path=/etc/letsencrypt/live/${config[server]}/fullchain.pem
if [[ -r "\${fullchain_path}" ]]; then
fullchain_fingerprint=\$(openssl x509 -noout -fingerprint -sha256 -in "\${fullchain_path}" 2>/dev/null | awk -F= '{print \$2}' | tr -d : | tr '[:upper:]' '[:lower:]')
installed_fingerprint=\$(openssl x509 -noout -fingerprint -sha256 -in /certificate/server.pem 2>/dev/null | awk -F= '{print \$2}' | tr -d : | tr '[:upper:]' '[:lower:]')
if [[ \$fullchain_fingerprint != \$installed_fingerprint ]]; then
/deployhook.sh /certificate ${compose_project} ${config[server]} ${service[server_crt]} "${service[server_pem]}"
fi
fi
while true; do
ls -d /website/* | grep -E '^/website/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\$'|xargs rm -f
uuid=\$(uuidgen); echo "\$uuid" > "/website/\$uuid"
response=\$(curl -skL --max-time 3 http://${config[server]}/\$uuid)
echo "\$response" | grep \$uuid >/dev/null && break
echo "Domain ${config[server]} is not pointing to the server"; sleep 5
done
ls -d /website/* | grep -E '^/website/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\$'|xargs rm -f
while true; do
certbot certonly -n --standalone --key-type ecdsa --elliptic-curve secp256r1 --agree-tos --register-unsafely-without-email -d ${config[server]} --deploy-hook "/deployhook.sh /certificate ${compose_project} ${config[server]} ${service[server_crt]} ${service[server_pem]}"
sleep 1h &; wait \${!}
done
EOF
}

function generate_certbot_deployhook {
cat >"${path[certbot_deployhook]}" <<EOF
#!/bin/sh
cert_path=\$1; compose_project=\$2; domain=\$3
renewed_path=/etc/letsencrypt/live/\$domain
cat "\$renewed_path/fullchain.pem" > "\$cert_path/server.crt"
cat "\$renewed_path/privkey.pem" > "\$cert_path/server.key"
cat "\$renewed_path/fullchain.pem" "\$renewed_path/privkey.pem" > "\$cert_path/server.pem"
i=4; while [ \$i -le \$# ]; do eval service=\\\${\$i}; docker compose -p "${compose_project}" restart --timeout 2 "\$service"; i=\$((i+1)); done
EOF
chmod +x "${path[certbot_deployhook]}"
}

function generate_certbot_dockerfile {
cat >"${path[certbot_dockerfile]}" <<EOF
FROM ${image[certbot]}
RUN apk add --no-cache docker-cli-compose curl uuidgen
EOF
}

function generate_tgbot_dockerfile {
cat >"${path[tgbot_dockerfile]}" <<EOF
FROM ${image[python]}WORKDIR /opt/reality-ezpz/tgbot
RUN apk add --no-cache docker-cli-compose curl bash newt libqrencode-tools sudo openssl jq zip unzip
RUN pip install --no-cache-dir python-telegram-bot==22.3 qrcode[pil]==8.2
CMD [ "python", "./tgbot.py" ]
EOF
}

function download_tgbot_script {
curl -fsSL -m 3 https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/tgbot.py -o "${path[tgbot_script]}"
}

function generate_selfsigned_certificate {
openssl ecparam -name prime256v1 -genkey -out "${path[server_key]}"
openssl req -new -key "${path[server_key]}" -out /tmp/server.csr -subj "/CN=${config[server]}"
openssl x509 -req -days 365 -in /tmp/server.csr -signkey "${path[server_key]}" -out "${path[server_crt]}"
cat "${path[server_key]}" "${path[server_crt]}" > "${path[server_pem]}"
rm -f /tmp/server.csr
}

function generate_engine_config {
local users_object="" reality_object="" tls_object="" warp_object="" reality_port=443 temp_file

[[ ${config[security]} == 'reality' && ${config[domain]} =~ ":" ]] && reality_port="${config[domain]#*:}"

reality_object='"security":"reality","realitySettings":{"show":false,"dest":"'"${config[domain]%%:*}"':'"${reality_port}"'","xver":0,"serverNames":["'"${config[domain]%%:*}"'"],"privateKey":"'"${config[private_key]}'"',"maxTimeDiff":60000,"shortIds":["'"${config[short_id]}'"']}'
tls_object='"security":"tls","tlsSettings":{"certificates":[{"oneTimeLoading":true,"certificateFile":"/etc/xray/server.crt","keyFile":"/etc/xray/server.key"}]}'

[[ ${config[warp]} == 'ON' ]] && warp_object='{
"protocol":"wireguard","tag":"warp","settings":{"secretKey":"'"${config[warp_private_key]}'"',"address":["'"${config[warp_interface_ipv4]}'"'/32","'"${config[warp_interface_ipv6]}'"'/128"],"peers":[{"endpoint":"engage.cloudflareclient.com:2408","publicKey":"bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo="}],"mtu":128}},'

# === HYSTERIA2 INBOUND ===
if [[ ${config[protocol]} == 'hysteria2' ]]; then
for user in "${!users[@]}"; do
[[ -n "$users_object" ]] && users_object="${users_object},"$'\n'
users_object="${users_object}{\"auth\":\"$(echo -n "${user}${users[${user}]}" | sha256sum | cut -d ' ' -f 1 | head -c 16)\",\"email\":\"${user}\"}"
done
cat >"${path[engine]}" <<EOF
{
"log":{"loglevel":"error"},
"dns":{"servers":[$([[ ${config[safenet]} == ON ]] && echo '"tcp+local://1.1.1.3","tcp+local://1.0.0.3"' || echo '"tcp+local://1.1.1.1","tcp+local://1.0.0.1"')]},
"inbounds":[{
"listen":"0.0.0.0","port":${config[port]},"protocol":"hysteria2","tag":"inbound",
"settings":{"version":2,"clients":[${users_object}]},
"streamSettings":{
"network":"hysteria2",
"security":"tls",
"tlsSettings":{"certificates":[{"oneTimeLoading":true,"certificateFile":"/etc/xray/server.crt","keyFile":"/etc/xray/server.key"}]},
"hysteriaSettings":{"version":2}
}
}],"outbounds":[
{"protocol":"freedom","tag":"internet"},
$([[ ${config[warp]} == ON ]] && echo "${warp_object}" || true)
{"protocol":"blackhole","tag":"block"}
],
"routing":{
"domainStrategy":"IPIfNonMatch",
"rules":[
{"type":"field","ip":["$([[ ${config[warp]} == OFF ]] && echo '"geoip:cn","geoip:ir",')"0.0.0.0/8","10.0.0.0/8","100.64.0.0/10","127.0.0.0/8","169.254.0.0/16","172.16.0.0/12","192.0.0.0/24","192.0.2.0/24","192.168.0.0/16","198.18.0.0/15","198.51.100.0/24","203.0.113.0/24","::1/128","fc00::/7","fe80::/10","geoip:private"],"outboundTag":"block"},
{"type":"field","port":"25,587,465,2525","network":"tcp","outboundTag":"block"},
{"type":"field","protocol":["bittorrent"],"outboundTag":"block"},
{"type":"field","outboundTag":"block","domain":[$([[ ${config[safenet]} == ON ]] && echo '"geosite:category-porn",' || true)"geosite:category-ads-all","domain:pushnotificationws.com","domain:sunlight-leds.com","domain:icecyber.org"]},
{"type":"field","inboundTag":"inbound","outboundTag":"$([[ ${config[warp]} == ON ]] && echo "warp" || echo "internet")"}
]},
"policy":{"levels":{"0":{"handshake":2,"connIdle":120}}}
}
EOF

# === VLESS INBOUND ===
else
for user in "${!users[@]}"; do
[[ -n "$users_object" ]] && users_object="${users_object},"$'\n'
users_object="${users_object}{\"id\":\"${users[${user}]}\",\"flow\":\"$([[ ${config[transport]} == 'tcp' ]] && echo 'xtls-rprx-vision' || true)\",\"email\":\"${user}\"}"
done
cat >"${path[engine]}" <<EOF
{
"log":{"loglevel":"error"},
"dns":{"servers":[$([[ ${config[safenet]} == ON ]] && echo '"tcp+local://1.1.1.3","tcp+local://1.0.0.3"' || echo '"tcp+local://1.1.1.1","tcp+local://1.0.0.1"')]},
"inbounds":[
{
"listen":"0.0.0.0","port":8080,"protocol":"dokodemo-door","settings":{"address":"${config[domain]%%:*}","port":80,"network":"tcp"}
},
{
"listen":"0.0.0.0","port":8443,"protocol":"vless","tag":"inbound",
"settings":{"clients":[${users_object}],"decryption":"none"},
"streamSettings":{
$([[ ${config[transport]} == 'grpc' ]] && echo '"grpcSettings":{"serviceName":"'"${config[service_path]}'"'},' || true)
$([[ ${config[transport]} == 'ws' ]] && echo '"wsSettings":{"headers":{"Host":"'"${config[host_header]}"'"},"path":"/'"${config[service_path]}'"'},' || true)
$([[ ${config[transport]} == 'http' ]] && echo '"httpSettings":{"host":["'"${config[server]}'"],"path":"/'"${config[service_path]}'"'},' || true)
$(if [[ ${config[transport]} == 'xhttp' ]]; then echo '"xhttpSettings":{'; [[ -n ${config[host_header]} ]] && echo '"host":"'"${config[host_header]}'"','; echo '"path":"/'"${config[service_path]}'"'}',; fi)
"network":"${config[transport]}",
$(if [[ ${config[security]} == 'reality' ]]; then echo "${reality_object}"; elif [[ ${config[security]} == 'notls' ]]; then echo '"security":"none"'; else echo "${tls_object}"; fi)
},
"sniffing":{"enabled":true,"destOverride":["http","tls"]}
}
],
"outbounds":[
{"protocol":"freedom","tag":"internet"},
$([[ ${config[warp]} == ON ]] && echo "${warp_object}" || true)
{"protocol":"blackhole","tag":"block"}],
"routing":{
"domainStrategy":"IPIfNonMatch",
"rules":[
{"type":"field","ip":["$([[ ${config[warp]} == OFF ]] && echo '"geoip:cn","geoip:ir",')"0.0.0.0/8","10.0.0.0/8","100.64.0.0/10","127.0.0.0/8","169.254.0.0/16","172.16.0.0/12","192.0.0.0/24","192.0.2.0/24","192.168.0.0/16","198.18.0.0/15","198.51.100.0/24","203.0.113.0/24","::1/128","fc00::/7","fe80::/10","geoip:private"],"outboundTag":"block"},
{"type":"field","port":"25,587,465,2525","network":"tcp","outboundTag":"block"},
{"type":"field","protocol":["bittorrent"],"outboundTag":"block"},
{"type":"field","outboundTag":"block","domain":[$([[ ${config[safenet]} == ON ]] && echo '"geosite:category-porn",' || true)"geosite:category-ads-all","domain:pushnotificationws.com","domain:sunlight-leds.com","domain:icecyber.org"]},
{"type":"field","inboundTag":"inbound","outboundTag":"$([[ ${config[warp]} == ON ]] && echo "warp" || echo "internet")"}
]},
"policy":{"levels":{"0":{"handshake":2,"connIdle":120}}}
}
EOF
fi

# Патч
if [[ -r ${config_path}/xray.patch ]]; then
jq empty ${config_path}/xray.patch || { echo "xray.patch не валидный JSON"; exit 1; }
temp_file=$(mktemp); jq -s add ${path[engine]} ${config_path}/xray.patch > ${temp_file}; mv ${temp_file} ${path[engine]}
fi
}

function generate_config {
generate_docker_compose
generate_engine_config
# Сертификаты для VLESS с TLS/letsencrypt или Hysteria2
if [[ (${config[protocol]} == 'vless' && ${config[security]} != 'reality' && ${config[security]} != 'notls') || (${config[protocol]} == 'hysteria2' && ${config[security]} != 'notls') ]]; then
mkdir -p "${config_path}/certificate"
[[ ${config[protocol]} == 'vless' && ${config[security]} != 'reality' && ${config[security]} != 'notls' ]] && generate_haproxy_config
[[ ! -r "${path[server_pem]}" || ! -r "${path[server_crt]}" || ! -r "${path[server_key]}" ]] && generate_selfsigned_certificate
fi
[[ ${config[protocol]} == 'vless' && ${config[security]} == 'letsencrypt' ]] && {
mkdir -p "${config_path}/certbot"; generate_certbot_deployhook; generate_certbot_dockerfile; generate_certbot_script
}
}

function get_ipv6 { curl -fsSL -m 3 --ipv6 https://cloudflare.com/cdn-cgi/trace 2>/dev/null | grep ip | cut -d '=' -f2; }

function print_client_configuration {
local username=$1 client_config ipv6 client_config_ipv6
if [[ ${config[protocol]} == 'hysteria2' ]]; then
client_config="hy2://"
client_config="${client_config}$(echo -n "${username}${users[${username}]}" | sha256sum | cut -d ' ' -f 1 | head -c 16)"
client_config="${client_config}@${config[server]}:${config[port]}"
client_config="${client_config}?sni=${config[domain]%%:*}"
[[ ${config[security]} == 'selfsigned' ]] && client_config="${client_config}&insecure=1"
client_config="${client_config}#${username}"
else
client_config="vless://${users[${username}]}@${config[server]}:${config[port]}?security=$([[ ${config[security]} == 'reality' ]] && echo reality || { [[ ${config[security]} == 'notls' ]] && echo none || echo tls; })&encryption=none&headerType=none&type=${config[transport]}"
[[ ${config[security]} != 'notls' ]] && {client_config="${client_config}&alpn=$([[ ${config[transport]} == 'ws' ]] && echo 'http/1.1' || echo 'h2,http/1.1')&fp=chrome&sni=${config[domain]%%:*}"
[[ ${config[transport]} == 'tcp' ]] && client_config="${client_config}&flow=xtls-rprx-vision"
}
[[ ${config[security]} == 'reality' ]] && client_config="${client_config}&pbk=${config[public_key]}&sid=${config[short_id]}"
[[ ${config[transport]} =~ ^(ws|http|xhttp)$ ]] && client_config="${client_config}&path=%2F${config[service_path]}"
[[ ${config[transport]} =~ ^(xhttp|ws|http)$ && -n ${config[host_header]} ]] && client_config="${client_config}&host=${config[host_header]}"
[[ ${config[transport]} == 'xhttp' ]] && client_config="${client_config}&mode=auto"
[[ ${config[transport]} == 'grpc' ]] && client_config="${client_config}&mode=gun&serviceName=${config[service_path]}"
client_config="${client_config}#${username}"
fi
echo ""; echo "=================================================="; echo "Конфигурация клиента:"; echo ""; echo "$client_config"; echo ""; echo "QR-код:"; echo ""; qrencode -t ansiutf8 "${client_config}"
ipv6=$(get_ipv6)
if [[ -n $ipv6 ]]; then
client_config_ipv6=$(echo "$client_config" | sed "s/@${config[server]}:/@[${ipv6}]:/" | sed "s/#${username}/#${username}-ipv6/")
echo ""; echo "==================IPv6==================="; echo "$client_config_ipv6"; echo ""; qrencode -t ansiutf8 "${client_config_ipv6}"
fi
}

function upgrade {
[[ -e "${HOME}/reality/config" ]] && { ${docker_cmd} --project-directory "${HOME}/reality" down --remove-orphans --timeout 2; mv -f "${HOME}/reality" ${config_path}; }
local uuid=$(grep '^uuid=' "${path[config]}" 2>/dev/null | cut -d= -f2 || true)
[[ -n $uuid ]] && { sed -i '/^uuid=/d' "${path[users]}"; echo "RealityEZPZ=${uuid}" >> "${path[users]}"; sed -i 's|=true|=ON|g; s|=false|=OFF|g' "${path[users]}"; }
rm -f "${config_path}/xray.conf"
! ${docker_cmd} ls | grep ${compose_project} >/dev/null && [[ -r ${path[compose]} ]] && ${docker_cmd} --project-directory ${config_path} down --remove-orphans --timeout 2
[[ -r ${path[config]} ]] && { sed -i 's|transport=h2|transport=http|g; s|security=tls-invalid|security=selfsigned|g; s|security=tls-valid|security=letsencrypt|g' "${path[config]}"; }
for key in "${!path[@]}"; do [[ -d "${path[$key]}" ]] && rm -rf "${path[$key]}"; done
[[ -d "${config_path}/warp" ]] && { ${docker_cmd} --project-directory ${config_path} -p ${compose_project} down --remove-orphans --timeout 2 || true; local warp_token=$(cat ${config_path}/warp/reg.json | jq -r '.api_token'); local warp_id=$(cat ${config_path}/warp/reg.json | jq -r '.registration_id'); warp_api "DELETE" "/reg/${warp_id}" "" "${warp_token}" >/dev/null 2>&1 || true; rm -rf "${config_path}/warp"; }
}

# === MENUS ===
function main_menu {
local selection
while true; do
selection=$(whiptail --clear --backtitle "$BACKTITLE" --title "Управление сервером" --menu "$MENU" $HEIGHT $WIDTH $CHOICE_HEIGHT --ok-button "Выбрать" --cancel-button "Выход" \
"1" "Добавить пользователя" "2" "Удалить пользователя" "3" "Просмотр пользователя" "4" "Конфигурация сервера" "5" "Настройки" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
case $selection in
1) add_user_menu ;; 2) delete_user_menu ;; 3) view_user_menu ;; 4) view_config_menu ;; 5) configuration_menu ;;
esac
done
}

function add_user_menu {
local username message
while true; do
username=$(whiptail --clear --backtitle "$BACKTITLE" --title "Добавить пользователя" --inputbox "Имя пользователя:" $HEIGHT $WIDTH 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ! $username =~ ${regex[username]} ]] && { message_box "Ошибка" "Имя: A-Z, a-z, 0-9"; continue; }
[[ -n ${users[$username]} ]] && { message_box "Ошибка" "Пользователь существует"; continue; }
users[$username]=$(cat /proc/sys/kernel/random/uuid); update_users_filewhiptail --clear --backtitle "$BACKTITLE" --title "Добавить пользователя" --yes-button "Просмотр" --no-button "Назад" --yesno "Пользователь ${username} создан." $HEIGHT $WIDTH 3>&1 1>&2 2>&3
[[ $? -ne 0 ]] && break; view_user_menu "${username}"
done
}

function delete_user_menu {
local username
while true; do
username=$(list_users_menu "Удалить пользователя"); [[ $? -ne 0 ]] && return 0
[[ ${#users[@]} -eq 1 ]] && { message_box "Ошибка" "Нельзя удалить единственного пользователя"; continue; }
whiptail --clear --backtitle "$BACKTITLE" --title "Удалить" --yesno "Удалить $username?" $HEIGHT $WIDTH 3>&1 1>&2 2>&3; [[ $? -ne 0 ]] && continue
unset users["${username}"]; update_users_file; message_box "Удалено" "Пользователь ${username} удалён."
done
}

function view_user_menu {
local username user_config
while true; do
[[ $# -gt 0 ]] && username=$1 || { username=$(list_users_menu "Просмотр"); [[ $? -ne 0 ]] && return 0; }
if [[ ${config[protocol]} == 'hysteria2' ]]; then
user_config="Протокол: hysteria2
Пользователь: ${username}
Адрес: ${config[server]}
Порт: ${config[port]}/UDP
Пароль: $(echo -n "${username}${users[${username}]}" | sha256sum | cut -d ' ' -f 1 | head -c 16)
SNI: ${config[domain]%%:*} (опционально)
Безопасность: ${config[security]}"
else
user_config="Протокол: vless
Пользователь: ${username}
Адрес: ${config[server]}
Порт: ${config[port]}
ID: ${users[$username]}
Транспорт: ${config[transport]}
$([[ ${config[transport]} =~ ^(ws|http|xhttp)$ ]] && echo "Путь: /${config[service_path]}
Host: ${config[host_header]:-${config[server]}}")
$([[ ${config[transport]} == 'grpc' ]] && echo "gRPC serviceName: ${config[service_path]}
Режим: gun")
Безопасность: ${config[security]}
SNI: ${config[domain]%%:*}
$([[ ${config[security]} == 'reality' ]] && echo "PublicKey: ${config[public_key]}
ShortId: ${config[short_id]}")"
fi
whiptail --clear --backtitle "$BACKTITLE" --title "Детали ${username}" --yes-button "QR-код" --no-button "Назад" --yesno "${user_config}" $HEIGHT $WIDTH 3>&1 1>&2 2>&3
[[ $? -eq 0 ]] && { clear; print_client_configuration "${username}"; echo; echo "Enter для возврата..."; read; clear; }
[[ $# -gt 0 ]] && return 0
done
}

function list_users_menu {local title=$1 options selection
options=$(dict_expander users)
selection=$(whiptail --clear --noitem --backtitle "$BACKTITLE" --title "$title" --menu "Пользователь" $HEIGHT $WIDTH $CHOICE_HEIGHT $options 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && return 1; echo "${selection}"
}

function show_server_config {
echo "Протокол: ${config[protocol]}
Сервер: ${config[server]}
Порт: ${config[port]} $([[ ${config[protocol]} == 'hysteria2' ]] && echo '/UDP')
SNI: ${config[domain]}
$([[ ${config[protocol]} == 'vless' ]] && echo "Транспорт: ${config[transport]}
Путь: /${config[service_path]}
Host: ${config[host_header]}")
Безопасность: ${config[security]}
SafeNet: ${config[safenet]}
WARP: ${config[warp]}
$([[ ${config[warp]} == ON ]] && echo "Лицензия: ${config[warp_license]}")
Telegram-бот: ${config[tgbot]}"
}

function view_config_menu { message_box "Конфигурация" "$(show_server_config)"; }

function restart_menu {
whiptail --clear --backtitle "$BACKTITLE" --title "Перезапуск" --yesno "Перезапустить службы?" $HEIGHT $WIDTH 3>&1 1>&2 2>&3; [[ $? -ne 0 ]] && return
restart_docker_compose; [[ ${config[tgbot]} == 'ON' ]] && restart_tgbot_compose
}

function regenerate_menu {
whiptail --clear --backtitle "$BACKTITLE" --title "Ключи" --yesno "Перегенерировать ключи?" $HEIGHT $WIDTH 3>&1 1>&2 2>&3; [[ $? -ne 0 ]] && return
generate_keys; config[public_key]=${config_file[public_key]}; config[private_key]=${config_file[private_key]}; config[short_id]=${config_file[short_id]}; update_config_file
message_box "Готово" "Ключи перегенерированы."
}

function restore_defaults_menu {
whiptail --clear --backtitle "$BACKTITLE" --title "Сброс" --yesno "Сбросить настройки?" $HEIGHT $WIDTH 3>&1 1>&2 2>&3; [[ $? -ne 0 ]] && return
restore_defaults; update_config_file; message_box "Готово" "Настройки сброшены."
}

function configuration_menu {
local selection
while true; do
selection=$(whiptail --clear --backtitle "$BACKTITLE" --title "Настройки" --menu "Опция:" $HEIGHT $WIDTH $CHOICE_HEIGHT \
"1" "Протокол" "2" "Адрес сервера" "3" "Порт" "4" "Безопасность" "5" "SNI Домен" \
$([[ ${config[protocol]} == 'vless' ]] && echo '"6" "Транспорт (vless)" ') \
$([[ ${config[protocol]} == 'vless' ]] && echo '"7" "Путь (vless)" ') \
$([[ ${config[protocol]} == 'vless' ]] && echo '"8" "Host (vless)" ') \
"9" "WARP" "10" "SafeNet" "11" "Telegram-бот" "12" "Перезапуск" "13" "Ключи" "14" "Сброс" "15" "Бэкап" "16" "Восстановить" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
case $selection in1) config_protocol_menu ;; 2) config_server_menu ;; 3) config_port_menu ;; 4) config_security_menu ;; 5) config_sni_domain_menu ;;
6) [[ ${config[protocol]} == 'vless' ]] && config_transport_menu ;;
7) [[ ${config[protocol]} == 'vless' ]] && config_path_menu ;;
8) [[ ${config[protocol]} == 'vless' ]] && config_host_menu ;;
9) config_warp_menu ;; 10) config_safenet_menu ;; 11) config_tgbot_menu ;; 12) restart_menu ;; 13) regenerate_menu ;; 14) restore_defaults_menu ;; 15) backup_menu ;; 16) restore_backup_menu ;;
esac
done
}

function config_protocol_menu {
local protocol
protocol=$(whiptail --clear --backtitle "$BACKTITLE" --title "Протокол" --radiolist --noitem "Выберите протокол:" $HEIGHT $WIDTH $CHOICE_HEIGHT \
"vless" "$([[ "${config[protocol]}" == 'vless' ]] && echo 'on' || echo 'off')" \
"hysteria2" "$([[ "${config[protocol]}" == 'hysteria2' ]] && echo 'on' || echo 'off')" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && return
config[protocol]=$protocol
# Сброс несовместимых настроек
[[ $protocol == 'hysteria2' ]] && { config[transport]=''; config[security]=$([[ ${config[security]} == 'notls' ]] && echo 'selfsigned' || echo ${config[security]}); }
update_config_file
}

function config_server_menu {
local server
while true; do
server=$(whiptail --clear --backtitle "$BACKTITLE" --title "Адрес сервера" --inputbox "IP или домен:" $HEIGHT $WIDTH "${config[server]}" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ! ${server} =~ ${regex[domain]} && ${config[security]} == 'letsencrypt' ]] && { message_box "Ошибка" "Для letsencrypt нужен домен"; continue; }
[[ -z ${server} ]] && server="${defaults[server]}"
config[server]="${server}"; [[ ${config[security]} != 'reality' && ${config[security]} != 'notls' ]] && config[domain]="${server}"
update_config_file; break
done
}

function config_transport_menu {
[[ ${config[protocol]} != 'vless' ]] && return
local transport
transport=$(whiptail --clear --backtitle "$BACKTITLE" --title "Транспорт (VLESS)" --radiolist --noitem "Транспорт:" $HEIGHT $WIDTH $CHOICE_HEIGHT \
"tcp" "$([[ "${config[transport]}" == 'tcp' ]] && echo 'on' || echo 'off')" \
"http" "$([[ "${config[transport]}" == 'http' ]] && echo 'on' || echo 'off')" \
"xhttp" "$([[ "${config[transport]}" == 'xhttp' ]] && echo 'on' || echo 'off')" \
"grpc" "$([[ "${config[transport]}" == 'grpc' ]] && echo 'on' || echo 'off')" \
"ws" "$([[ "${config[transport]}" == 'ws' ]] && echo 'on' || echo 'off')" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && return
[[ ${transport} == 'ws' && ${config[security]} == 'reality' ]] && { message_box "Ошибка" "WS + Reality несовместимы"; return; }
config[transport]=$transport; update_config_file
}

function config_sni_domain_menu {
local sni_domain
while true; dosni_domain=$(whiptail --clear --backtitle "$BACKTITLE" --title "SNI Домен" --inputbox "SNI:" $HEIGHT $WIDTH "${config[domain]}" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ! $sni_domain =~ ${regex[domain_port]} ]] && { message_box "Ошибка" "Неверный домен"; continue; }
config[domain]=$sni_domain; update_config_file; break
done
}

function config_security_menu {
local security free_80=true
while true; do
security=$(whiptail --clear --backtitle "$BACKTITLE" --title "Безопасность" --radiolist --noitem "Тип:" $HEIGHT $WIDTH $CHOICE_HEIGHT \
"reality" "$([[ "${config[security]}" == 'reality' ]] && echo 'on' || echo 'off')" \
"letsencrypt" "$([[ "${config[security]}" == 'letsencrypt' ]] && echo 'on' || echo 'off')" \
"selfsigned" "$([[ "${config[security]}" == 'selfsigned' ]] && echo 'on' || echo 'off')" \
$([[ ${config[protocol]} == 'vless' ]] && echo '"notls" "'$([[ "${config[security]}" == 'notls' ]] && echo 'on' || echo 'off')'"') 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ${config[protocol]} == 'hysteria2' && $security == 'notls' ]] && { message_box "Ошибка" "Hysteria2 требует TLS"; continue; }
[[ ! ${config[server]} =~ ${regex[domain]} && $security == 'letsencrypt' ]] && { message_box "Ошибка" "Для letsencrypt нужен домен в --server"; continue; }
[[ ${config[protocol]} == 'vless' && ${config[transport]} == 'ws' && $security == 'reality' ]] && { message_box "Ошибка" "WS + Reality несовместимы"; continue; }
[[ $security == 'letsencrypt' && ${config[port]} -ne 443 ]] && {
lsof -i :80 >/dev/null 2>&1 || { for container in $(${docker_cmd} -p ${compose_project} ps -q); do docker port "${container}"| grep '0.0.0.0:80' >/dev/null 2>&1 && { free_80=true; break; }; done; }
[[ ${free_80} != 'true' ]] && { message_box "Ошибка" "Порт 80 должен быть свободен для letsencrypt"; continue; }
}
[[ $security != 'reality' && $security != 'notls' ]] && config[domain]="${config[server]}"
[[ $security == 'reality' || $security == 'notls' ]] && config[domain]="${defaults[domain]}"
config[security]="${security}"; update_config_file; break
done
}

function config_port_menu {
local port
while true; do
port=$(whiptail --clear --backtitle "$BACKTITLE" --title "Порт" --inputbox "Порт (1-65535):" $HEIGHT $WIDTH "${config[port]}" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ! $port =~ ${regex[port]} || $port -lt 1 || $port -gt 65535 ]] && { message_box "Ошибка" "Неверный порт"; continue; }
config[port]=$port; update_config_file; break
done
}

function config_path_menu {
[[ ${config[protocol]} != 'vless' ]] && return
local user_path
while true; do
user_path=$(whiptail --clear --backtitle "$BACKTITLE" --title "Путь (VLESS)" --inputbox "Путь (без /):" $HEIGHT $WIDTH "${config[service_path]}" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
config[service_path]="${user_path#/}"; update_config_file; break
done
}

function config_host_menu {[[ ${config[protocol]} != 'vless' ]] && return
local host
while true; do
host=$(whiptail --clear --backtitle "$BACKTITLE" --title "Host (VLESS)" --inputbox "Host заголовок:" $HEIGHT $WIDTH "${config[host_header]}" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
config[host_header]=$host; update_config_file; break
done
}

function config_safenet_menu {
local safenet
safenet=$(whiptail --clear --backtitle "$BACKTITLE" --title "SafeNet" --radiolist --noitem "Блокировка:" $HEIGHT $WIDTH $CHOICE_HEIGHT \
"Включить" "$([[ "${config[safenet]}" == 'ON' ]] && echo 'on' || echo 'off')" \
"Выключить" "$([[ "${config[safenet]}" == 'OFF' ]] && echo 'on' || echo 'off')" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && return
config[safenet]=$([[ $safenet == 'Включить' ]] && echo ON || echo OFF); update_config_file
}

function config_warp_menu {
local warp warp_license error temp_file exit_code old_warp=${config[warp]} old_warp_license=${config[warp_license]}
while true; do
warp=$(whiptail --clear --backtitle "$BACKTITLE" --title "WARP" --radiolist --noitem "WARP:" $HEIGHT $WIDTH $CHOICE_HEIGHT \
"Включить" "$([[ "${config[warp]}" == 'ON' ]] && echo 'on' || echo 'off')" \
"Выключить" "$([[ "${config[warp]}" == 'OFF' ]] && echo 'on' || echo 'off')" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ $warp == 'Выключить' ]] && { config[warp]=OFF; [[ -n ${config[warp_id]} && -n ${config[warp_token]} ]] && warp_delete_account "${config[warp_id]}" "${config[warp_token]}"; return; }
[[ -z ${config[warp_private_key]} || -z ${config[warp_token]} || -z ${config[warp_id]} || -z ${config[warp_client_id]} || -z ${config[warp_interface_ipv4]} || -z ${config[warp_interface_ipv6]} ]] && {
temp_file=$(mktemp); warp_create_account > "${temp_file}"; exit_code=$?; error=$(< "${temp_file}"); rm -f "${temp_file}"
[[ ${exit_code} -ne 0 ]] && { message_box "Ошибка" "${error}"; continue; }
}
config[warp]=ON
while true; do
warp_license=$(whiptail --clear --backtitle "$BACKTITLE" --title "WARP+ Лицензия" --inputbox "Лицензия (xxx-xxx-xxx):" $HEIGHT $WIDTH "${config[warp_license]}" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ! $warp_license =~ ${regex[warp_license]} ]] && { message_box "Ошибка" "Неверный формат"; continue; }
temp_file=$(mktemp); warp_add_license "${config[warp_id]}" "${config[warp_token]}" "${warp_license}" > "${temp_file}"; exit_code=$?; error=$(< "${temp_file}"); rm -f "${temp_file}"
[[ ${exit_code} -ne 0 ]] && { message_box "Ошибка" "${error}"; continue; }
return
done
done
config[warp]=$old_warp; config[warp_license]=$old_warp_license
}

function config_tgbot_menu {
local tgbot tgbot_token tgbot_admins old_tgbot=${config[tgbot]} old_tgbot_token=${config[tgbot_token]} old_tgbot_admins=${config[tgbot_admins]}
while true; do
tgbot=$(whiptail --clear --backtitle "$BACKTITLE" --title "Telegram-бот" --radiolist --noitem "Включить:" $HEIGHT $WIDTH $CHOICE_HEIGHT \
"Включить" "$([[ "${config[tgbot]}" == 'ON' ]] && echo 'on' || echo 'off')" \
"Выключить" "$([[ "${config[tgbot]}" == 'OFF' ]] && echo 'on' || echo 'off')" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break[[ $tgbot == 'Выключить' ]] && { config[tgbot]=OFF; update_config_file; return; }
config[tgbot]=ON
while true; do
tgbot_token=$(whiptail --clear --backtitle "$BACKTITLE" --title "Токен бота" --inputbox "Токен:" $HEIGHT $WIDTH "${config[tgbot_token]}" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ! $tgbot_token =~ ${regex[tgbot_token]} ]] && { message_box "Ошибка" "Неверный токен"; continue; }
curl -sSfL -m 3 "https://api.telegram.org/bot${tgbot_token}/getMe" >/dev/null 2>&1 || { message_box "Ошибка" "Токен недействителен"; continue; }
config[tgbot_token]=$tgbot_token
while true; do
tgbot_admins=$(whiptail --clear --backtitle "$BACKTITLE" --title "Админы бота" --inputbox "Админы (через запятую, без @):" $HEIGHT $WIDTH "${config[tgbot_admins]}" 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ! $tgbot_admins =~ ${regex[tgbot_admins]} ]] && { message_box "Ошибка" "Неверный формат"; continue; }
config[tgbot_admins]=$tgbot_admins; update_config_file; return
done
done
done
config[tgbot]=$old_tgbot; config[tgbot_token]=$old_tgbot_token; config[tgbot_admins]=$old_tgbot_admins
}

function backup_menu {
local backup_password result
backup_password=$(whiptail --clear --backtitle "$BACKTITLE" --title "Бэкап" --inputbox "Пароль (опционально):" $HEIGHT $WIDTH 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && return
result=$(backup "${backup_password}" 2>&1) && { clear; echo "Бэкап: ${result}"; echo "Действителен 3 дня."; echo; echo "Enter..."; read; clear; } || message_box "Ошибка" "${result}"
}

function restore_backup_menu {
local backup_file backup_password result
while true; do
backup_file=$(whiptail --clear --backtitle "$BACKTITLE" --title "Восстановить" --inputbox "Путь или URL:" $HEIGHT $WIDTH 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && break
[[ ! $backup_file =~ ${regex[file_path]} && ! $backup_file =~ ${regex[url]} ]] && { message_box "Ошибка" "Неверный путь"; continue; }
backup_password=$(whiptail --clear --backtitle "$BACKTITLE" --title "Пароль" --inputbox "Пароль (опционально):" $HEIGHT $WIDTH 3>&1 1>&2 2>&3)
[[ $? -ne 0 ]] && continue
result=$(restore "${backup_file}" "${backup_password}" 2>&1) && { parse_config_file; parse_users_file; build_config; update_config_file; update_users_file; message_box "Готово" "Восстановлено."; args[restart]=true; break; } || message_box "Ошибка" "${result}"
done
}

function restart_docker_compose {
${docker_cmd} --project-directory ${config_path} -p ${compose_project} down --remove-orphans --timeout 2 || true
${docker_cmd} --project-directory ${config_path} -p ${compose_project} up --build -d --remove-orphans
}

function restart_tgbot_compose {
${docker_cmd} --project-directory ${config_path}/tgbot -p ${tgbot_project} down --remove-orphans --timeout 2 || true
${docker_cmd} --project-directory ${config_path}/tgbot -p ${tgbot_project} up --build -d --remove-orphans
}

function restart_container {
[[ -z "$(${docker_cmd} ls | grep "${path[compose]}" | grep running || true)" ]] && { restart_docker_compose; return; }${docker_cmd} --project-directory ${config_path} -p ${compose_project} ps --services "$1" | grep "$1" && ${docker_cmd} --project-directory ${config_path} -p ${compose_project} restart --timeout 2 "$1"
}

function warp_api {
local verb=$1 resource=$2 data=$3 token=$4 team_token=$5 endpoint=https://api.cloudflareclient.com/v0a2158 temp_file error command headers=()
temp_file=$(mktemp)
headers=("User-Agent: okhttp/3.12.1" "CF-Client-Version: a-6.10-2158" "Content-Type: application/json")
[[ -n ${token} ]] && headers+=("Authorization: Bearer ${token}")
[[ -n ${team_token} ]] && headers+=("Cf-Access-Jwt-Assertion: ${team_token}")
command="curl -sLX ${verb} -m 3 -w '%{http_code}' -o ${temp_file} ${endpoint}${resource}"
for header in "${headers[@]}"; do command+=" -H '${header}'"; done
[[ -n ${data} ]] && command+=" -d '${data}'"
response_code=$(( $(eval "${command}" || true) )); response_body=$(cat "${temp_file}"); rm -f "${temp_file}"
[[ response_code -eq 0 ]] && return 1
[[ response_code -gt 399 ]] && { error=$(echo "${response_body}" | jq -r '.errors[0].message' 2>/dev/null || true); [[ ${error} != 'null' ]] && echo "${error}"; return 2; }
echo "${response_body}"
}

function warp_create_account {
local response
docker run --rm -v "${config_path}":/data "${image[wgcf]}" register --config /data/wgcf-account.toml --accept-tos || { echo "Ошибка WARP"; return 1; }
[[ ! -r ${config_path}/wgcf-account.toml ]] && { echo "Ошибка WARP"; return 1; }
config[warp_token]=$(cat ${config_path}/wgcf-account.toml | grep 'access_token' | cut -d "'" -f2)
config[warp_id]=$(cat ${config_path}/wgcf-account.toml | grep 'device_id' | cut -d "'" -f2)
config[warp_private_key]=$(cat ${config_path}/wgcf-account.toml | grep 'private_key' | cut -d "'" -f2)
rm -f ${config_path}/wgcf-account.toml
response=$(warp_api "GET" "/reg/${config[warp_id]}" "" "${config[warp_token]}") || { [[ -n ${response} ]] && echo "${response}"; return 1; }
config[warp_client_id]=$(echo "${response}" | jq -r '.config.client_id')
config[warp_interface_ipv4]=$(echo "${response}" | jq -r '.config.interface.addresses.v4')
config[warp_interface_ipv6]=$(echo "${response}" | jq -r '.config.interface.addresses.v6')
update_config_file
}

function warp_add_license {
local id=$1 token=$2 license=$3 data='{"license": "'$license'"}' response
response=$(warp_api "PUT" "/reg/${id}/account" "${data}" "${token}") || { [[ -n ${response} ]] && echo "${response}"; return 1; }
config[warp_license]=${license}; update_config_file
}

function warp_delete_account {
local id=$1 token=$2
warp_api "DELETE" "/reg/${id}" "" "${token}" >/dev/null 2>&1 || true
config[warp_private_key]=""; config[warp_token]=""; config[warp_id]=""; config[warp_client_id]=""; config[warp_interface_ipv4]=""; config[warp_interface_ipv6]=""
update_config_file
}

function check_reload {
declare -A restart; generate_config
for key in "${!path[@]}"; do [[ "${md5["$key"]}" != $(get_md5 "${path[$key]}") ]] && { restart["${service["$key"]}"]='true'; md5["$key"]=$(get_md5 "${path[$key]}"); }; done
[[ "${restart[tgbot]}" == 'true' && "${config[tgbot]}" == 'ON' ]] && restart_tgbot_compose[[ "${config[tgbot]}" == 'OFF' ]] && ${docker_cmd} --project-directory ${config_path}/tgbot -p ${tgbot_project} down --remove-orphans --timeout 2 >/dev/null 2>&1 || true
[[ "${restart[compose]}" == 'true' ]] && { restart_docker_compose; return; }
for key in "${!restart[@]}"; do [[ $key != 'none' && $key != 'tgbot' ]] && restart_container "${key}"; done
}

function message_box { whiptail --clear --backtitle "$BACKTITLE" --title "$1" --msgbox "$2" $HEIGHT $WIDTH 3>&1 1>&2 2>&3; }
function get_md5 { md5sum "$1" 2>/dev/null | cut -f1 -d' ' || true; }

function generate_file_list {
path[config]="${config_path}/config"; path[users]="${config_path}/users"; path[compose]="${config_path}/docker-compose.yml"; path[engine]="${config_path}/engine.conf"
path[haproxy]="${config_path}/haproxy.cfg"; path[certbot_deployhook]="${config_path}/certbot/deployhook.sh"; path[certbot_dockerfile]="${config_path}/certbot/Dockerfile"; path[certbot_startup]="${config_path}/certbot/startup.sh"
path[server_pem]="${config_path}/certificate/server.pem"; path[server_key]="${config_path}/certificate/server.key"; path[server_crt]="${config_path}/certificate/server.crt"
path[tgbot_script]="${config_path}/tgbot/tgbot.py"; path[tgbot_dockerfile]="${config_path}/tgbot/Dockerfile"; path[tgbot_compose]="${config_path}/tgbot/docker-compose.yml"
service[config]='none'; service[users]='none'; service[compose]='compose'; service[engine]='engine'; service[haproxy]='haproxy'; service[certbot_deployhook]='certbot'; service[certbot_dockerfile]='compose'; service[certbot_startup]='certbot'; service[server_pem]='haproxy'; service[server_key]='engine'; service[server_crt]='engine'; service[tgbot_script]='tgbot'; service[tgbot_dockerfile]='compose'; service[tgbot_compose]='tgbot'
for key in "${!path[@]}"; do md5["$key"]=$(get_md5 "${path[$key]}"); done
}

function tune_kernel {
cat >/etc/sysctl.d/99-reality-ezpz.conf <<EOF
fs.file-max = 200000
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.core.netdev_max_backlog = 250000
net.core.somaxconn = 4096
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 10
net.ipv4.tcp_keepalive_time = 600
net.ipv4.ip_local_port_range = 10000 65000
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.tcp_max_tw_buckets = 5000
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_mem = 25600 51200 102400
net.ipv4.tcp_rmem = 4096 65536 67108864
net.ipv4.tcp_wmem = 4096 65536 67108864
net.ipv4.tcp_mtu_probing = 1
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.netfilter.nf_conntrack_max=1000000
EOF
sysctl -qp /etc/sysctl.d/99-reality-ezpz.conf >/dev/null 2>&1 || true
}

function configure_docker {
local docker_config="/etc/docker/daemon.json" config_modified=false temp_file
temp_file=$(mktemp)
if [[ ! -f "${docker_config}" ]] || [[ ! -s "${docker_config}" ]]; then
echo '{"experimental": true, "ip6tables": true}' | jq . > "${docker_config}"; config_modified=true
else
jq . "${docker_config}" &>/dev/null || { echo '{"experimental": true, "ip6tables": true}' | jq . > "${docker_config}"; config_modified=true; } || {jq 'if .experimental != true or .ip6tables != true then .experimental = true | .ip6tables = true else . end' "${docker_config}" | jq . > "${temp_file}" && { cmp --silent "${docker_config}" "${temp_file}" || { mv "${temp_file}" "${docker_config}"; config_modified=true; }; }; }
fi
rm -f "${temp_file}"
[[ "${config_modified}" = true ]] || ! systemctl is-active --quiet docker && sudo systemctl restart docker || true
}

# === MAIN ===
parse_args "$@" || show_help
[[ $EUID -ne 0 ]] && { echo "Запуск от root."; exit 1; }

[[ ${args[backup]} == true ]] && { backup_url=$(backup "${args[backup_password]:-}"); [[ $? -eq 0 ]] && { echo "Бэкап: ${backup_url}"; echo "Действителен 3 дня."; exit 0; }; }
[[ -n ${args[restore]} ]] && { restore "${args[restore]}" "${args[backup_password]:-}" && { args[restart]=true; echo "Восстановлено."; }; echo "Enter..."; read; clear; }

generate_file_list; install_packages; install_docker; configure_docker; upgrade; parse_config_file; parse_users_file; build_config; update_config_file; update_users_file; tune_kernel

[[ ${args[menu]} == 'true' ]] && { set +e; main_menu; set -e; }
[[ ${args[restart]} == 'true' ]] && { restart_docker_compose; [[ ${config[tgbot]} == 'ON' ]] && restart_tgbot_compose; }
[[ -z "$(${docker_cmd} ls | grep "${path[compose]}" | grep running || true)" ]] && restart_docker_compose
[[ -z "$(${docker_cmd} ls | grep "${path[tgbot_compose]}" | grep running || true)" && ${config[tgbot]} == 'ON' ]] && restart_tgbot_compose

[[ ${args[server-config]} == true ]] && { show_server_config; exit 0; }
[[ -n ${args[list_users]} ]] && { for user in "${!users[@]}"; do echo "${user}"; done; exit 0; }

[[ ${#users[@]} -eq 1 ]] && username="${!users[@]}"
[[ -n ${args[show_config]} ]] && { username="${args[show_config]}"; [[ -z "${users["${username}"]}" ]] && { echo "Пользователь не найден"; exit 1; }; }
[[ -n ${args[add_user]} ]] && username="${args[add_user]}"
[[ -n $username ]] && print_client_configuration "${username}"

echo "Готово!"; exit 0