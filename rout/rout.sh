#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

CONF_DIR="/etc/rout"
CONF_FILE="$CONF_DIR/relays.conf"
UPDATE_SCRIPT="/usr/local/bin/rout-update"
CRON_FILE="/etc/cron.d/rout"
SELF_PATH="/usr/local/bin/rout"

msg()  { echo -e "\( {CYAN}[*] \){NC} $1"; }
ok()   { echo -e "\( {GREEN}[✓] \){NC} $1"; }
warn() { echo -e "\( {YELLOW}[!] \){NC} $1"; }
err()  { echo -e "\( {RED}[✗] \){NC} $1"; }

get_script_path() {
    readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0"
}

check_root() {
    [[ $EUID -ne 0 ]] && { err "Запустите скрипт от root (sudo)"; exit 1; }
}

is_ip() {
    [[ "$1" =\~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] && return 0
    return 1
}

resolve_host() {
    local host=$1 ip
    if is_ip "$host"; then
        echo "$host"
        return
    fi
    ip=$(getent hosts "$host" 2>/dev/null | awk '{print $1; exit}')
    [[ -z "\( ip" ]] && ip= \)(dig +short "$host" 2>/dev/null | grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1)
    echo "$ip"
}

get_my_ip() {
    curl -s --max-time 5 ifconfig.me 2>/dev/null ||
    curl -s --max-time 5 api.ipify.org 2>/dev/null ||
    echo "<IP сервера>"
}

ufw_action() {
    local action=$1 port=$2 proto=$3
    command -v ufw &>/dev/null && ufw status | grep -q "Status: active" || return
    case $proto in
        tcp)     ufw "$action" "$port/tcp" >/dev/null 2>&1 ;;
        udp)     ufw "$action" "$port/udp" >/dev/null 2>&1 ;;
        tcp+udp) ufw "$action" "$port/tcp" >/dev/null 2>&1
                 ufw "$action" "$port/udp" >/dev/null 2>&1 ;;
    esac
    [[ $action == "allow" ]] && ok "UFW: порт $port/$proto открыт"
    [[ $action == "delete allow" ]] && ok "UFW: порт $port/$proto закрыт"
}

enable_forwarding() {
    grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf 2>/dev/null || \
        echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
    sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1
    ok "IP forwarding включён"
}

save_rules() {
    if command -v netfilter-persistent >/dev/null; then
        netfilter-persistent save >/dev/null 2>&1
        ok "Правила сохранены (netfilter-persistent)"
    elif command -v iptables-save >/dev/null; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4
        ok "Правила сохранены → /etc/iptables/rules.v4"
    else
        warn "Установите iptables-persistent для автосохранения"
    fi
}

install_deps() {
    local pkgs=()
    command -v iptables &>/dev/null || pkgs+=(iptables)
    command -v dig &>/dev/null || pkgs+=(dnsutils)
    command -v curl &>/dev/null || pkgs+=(curl)
    command -v netfilter-persistent &>/dev/null || pkgs+=(iptables-persistent)

    if [[ ${#pkgs[@]} -gt 0 ]]; then
        msg "Устанавливаю: ${pkgs[*]}"
        DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkgs[@]}" >/dev/null 2>&1
    fi
}

conf_init() {
    mkdir -p "$CONF_DIR"
    [[ -f "$CONF_FILE" ]] || touch "$CONF_FILE"
    chmod 600 "$CONF_FILE"
}

conf_add() {
    local id=$1 in_port=$2 proto=$3 target_host=$4 target_port=$5 target_ip=$6
    echo "$id|$in_port|$proto|$target_host|$target_port|\( target_ip| \)(date '+%Y-%m-%d %H:%M:%S')" >> "$CONF_FILE"
}

conf_remove() {
    sed -i "/^$1|/d" "$CONF_FILE"
}

conf_list() {
    [[ -f "$CONF_FILE" ]] && cat "$CONF_FILE" || true
}

conf_get() {
    grep "^$1|" "$CONF_FILE" 2>/dev/null
}

next_id() {
    local max=0 id
    while IFS='|' read -r id _; do
        [[ \( id =\~ ^[0-9]+ \) ]] && (( id > max )) && max=$id
    done < <(conf_list)
    echo $(( max + 1 ))
}

apply_dnat() {
    local in_port=$1 proto=$2 target_ip=$3 target_port=$4
    case $proto in
        tcp)
            iptables -t nat -A PREROUTING -p tcp --dport "$in_port" -j DNAT --to-destination "$target_ip:$target_port"
            ;;
        udp)
            iptables -t nat -A PREROUTING -p udp --dport "$in_port" -j DNAT --to-destination "$target_ip:$target_port"
            ;;
        tcp+udp)
            iptables -t nat -A PREROUTING -p tcp --dport "$in_port" -j DNAT --to-destination "$target_ip:$target_port"
            iptables -t nat -A PREROUTING -p udp --dport "$in_port" -j DNAT --to-destination "$target_ip:$target_port"
            ;;
    esac
    iptables -t nat -C POSTROUTING -j MASQUERADE 2>/dev/null || \
        iptables -t nat -A POSTROUTING -j MASQUERADE
}

remove_dnat() {
    local in_port=$1 proto=$2 target_ip=$3 target_port=$4
    case $proto in
        tcp)
            iptables -t nat -D PREROUTING -p tcp --dport "$in_port" -j DNAT --to-destination "$target_ip:$target_port" 2>/dev/null
            ;;
        udp)
            iptables -t nat -D PREROUTING -p udp --dport "$in_port" -j DNAT --to-destination "$target_ip:$target_port" 2>/dev/null
            ;;
        tcp+udp)
            iptables -t nat -D PREROUTING -p tcp --dport "$in_port" -j DNAT --to-destination "$target_ip:$target_port" 2>/dev/null
            iptables -t nat -D PREROUTING -p udp --dport "$in_port" -j DNAT --to-destination "$target_ip:$target_port" 2>/dev/null
            ;;
    esac
}

install_cron() {
    cat > "$UPDATE_SCRIPT" << 'EOFSCRIPT'
#!/bin/bash
CONF_FILE="/etc/rout/relays.conf"
LOG="/var/log/rout-update.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }
[[ -f "$CONF_FILE" ]] || exit 0
TMP_CONF=$(mktemp)
while IFS='|' read -r id in_port proto target_host target_port old_ip added_at; do
    if [[ "\( target_host" =\~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3} \) ]]; then
        echo "$id|$in_port|$proto|$target_host|$target_port|$old_ip|$added_at" >> "$TMP_CONF"
        continue
    fi
    new_ip=$(getent hosts "$target_host" 2>/dev/null | awk '{print $1; exit}')
    [[ -z "\( new_ip" ]] && new_ip= \)(dig +short "$target_host" 2>/dev/null | grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1)
    if [[ -z "$new_ip" ]]; then
        log "[$id] Не удалось разрезолвить $target_host"
        echo "$id|$in_port|$proto|$target_host|$target_port|$old_ip|$added_at" >> "$TMP_CONF"
        continue
    fi
    if [[ "$new_ip" != "$old_ip" ]]; then
        log "[$id] $target_host: IP изменился $old_ip → $new_ip"
        case $proto in
            tcp)     iptables -t nat -D PREROUTING -p tcp --dport "$in_port" -j DNAT --to-destination "$old_ip:$target_port" 2>/dev/null ;;
            udp)     iptables -t nat -D PREROUTING -p udp --dport "$in_port" -j DNAT --to-destination "$old_ip:$target_port" 2>/dev/null ;;
            tcp+udp) iptables -t nat -D PREROUTING -p tcp --dport "$in_port" -j DNAT --to-destination "$old_ip:$target_port" 2>/dev/null
                     iptables -t nat -D PREROUTING -p udp --dport "$in_port" -j DNAT --to-destination "$old_ip:$target_port" 2>/dev/null ;;
        esac
        case $proto in
            tcp)     iptables -t nat -A PREROUTING -p tcp --dport "$in_port" -j DNAT --to-destination "$new_ip:$target_port" ;;
            udp)     iptables -t nat -A PREROUTING -p udp --dport "$in_port" -j DNAT --to-destination "$new_ip:$target_port" ;;
            tcp+udp) iptables -t nat -A PREROUTING -p tcp --dport "$in_port" -j DNAT --to-destination "$new_ip:$target_port"
                     iptables -t nat -A PREROUTING -p udp --dport "$in_port" -j DNAT --to-destination "$new_ip:$target_port" ;;
        esac
        echo "$id|$in_port|$proto|$target_host|$target_port|\( new_ip| \)(date '+%Y-%m-%d %H:%M:%S')" >> "$TMP_CONF"
        log "[$id] Обновлено: $new_ip"
    else
        echo "$id|$in_port|$proto|$target_host|$target_port|$old_ip|$added_at" >> "$TMP_CONF"
    fi
done < "$CONF_FILE"
mv "$TMP_CONF" "$CONF_FILE"
chmod 600 "$CONF_FILE"
if command -v netfilter-persistent >/dev/null; then
    netfilter-persistent save >/dev/null 2>&1
else
    iptables-save > /etc/iptables/rules.v4 2>/dev/null
fi
EOFSCRIPT

    chmod +x "$UPDATE_SCRIPT"
    echo "*/10 * * * * root $UPDATE_SCRIPT" > "$CRON_FILE"
    ok "Cron установлен (каждые 10 минут)"
}

remove_cron() {
    rm -f "$CRON_FILE" "$UPDATE_SCRIPT"
    ok "Cron удалён"
}

show_status() {
    local my_ip=$(get_my_ip)
    echo ""
    echo -e "\( {BOLD} \){CYAN}╔═══════════════════════════════════════════════════╗${NC}"
    echo -e "\( {BOLD} \){CYAN}║                 rout — статус                     ║${NC}"
    echo -e "\( {BOLD} \){CYAN}╚═══════════════════════════════════════════════════╝${NC}"
    echo -e "  IP сервера: ${BOLD}\( my_ip \){NC}"
    echo ""

    local relays=$(conf_list)
    if [[ -z "$relays" ]]; then
        echo -e "  \( {YELLOW}Нет активных relay \){NC}"
    else
        printf "  %-4s %-8s %-10s %-30s %-8s %-16s %s\n" "ID" "Порт" "Протокол" "Target" "Порт" "IP" "Добавлен"
        echo "  ──────────────────────────────────────────────────────────────────────────────"
        while IFS='|' read -r id in_port proto target_host target_port target_ip added_at; do
            local active="\( {RED}✗ \){NC}"
            iptables -t nat -S PREROUTING 2>/dev/null | grep -q "DNAT.*$target_ip:\( target_port" && active=" \){GREEN}✓${NC}"
            printf "  %-4s %-8s %-10s %-30s %-8s %-16s %s %b\n" \
                "$id" "$in_port" "$proto" "$target_host" "$target_port" "$target_ip" "$added_at" "$active"
        done <<< "$relays"
    fi
    echo ""
    local fwd=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)
    echo -e "  IP forwarding: $([[ "\( fwd" == 1 ]] && echo -e " \){GREEN}включён\( {NC}" || echo -e " \){RED}выключен${NC}")"
    [[ -f "$CRON_FILE" ]] && echo -e "  Автообновление IP: \( {GREEN}активно \){NC}" || echo -e "  Автообновление IP: \( {RED}отключено \){NC}"
    echo ""
}

do_install() {
    echo -e "\n\( {BOLD}  Добавить relay \){NC}\n\( {BOLD}═══════════════════════ \){NC}\n"

    local my_ip=$(get_my_ip)
    echo -e "  IP сервера: ${BOLD}\( my_ip \){NC}\n"

    read -rp "  Target (домен или IP): " TARGET_HOST
    [[ -z "$TARGET_HOST" ]] && { err "Target обязателен"; return 1; }

    read -rp "  Target порт [4500]: " TARGET_PORT
    TARGET_PORT=${TARGET_PORT:-4500}

    read -rp "  Входящий порт [$TARGET_PORT]: " IN_PORT
    IN_PORT=${IN_PORT:-$TARGET_PORT}

    echo -e "\n  Протокол:"
    echo -e "    1) UDP\n    2) TCP\n    3) TCP+UDP"
    read -rp "  Выбор [1]: " p
    case ${p:-1} in
        2) PROTO="tcp" ;;
        3) PROTO="tcp+udp" ;;
        *) PROTO="udp" ;;
    esac

    echo -e "\n  \( {BOLD}Конфигурация: \){NC}"
    echo -e "  Входящий → \( {my_ip}: \){IN_PORT}/${PROTO}"
    echo -e "  Target   → \( {TARGET_HOST}: \){TARGET_PORT}\n"

    read -rp "  Продолжить? [y/N]: " confirm
    [[ "\( confirm" =\~ ^[Yy] \) ]] || { warn "Отменено"; return; }

    install_deps
    conf_init

    msg "Резолвим $TARGET_HOST..."
    TARGET_IP=$(resolve_host "$TARGET_HOST")
    [[ -z "$TARGET_IP" ]] && { err "Не удалось получить IP"; return 1; }
    ok "→ $TARGET_IP"

    enable_forwarding
    apply_dnat "$IN_PORT" "$PROTO" "$TARGET_IP" "$TARGET_PORT"
    ufw_action "allow" "$IN_PORT" "$PROTO"

    local id=$(next_id)
    conf_add "$id" "$IN_PORT" "$PROTO" "$TARGET_HOST" "$TARGET_PORT" "$TARGET_IP"

    if ! is_ip "$TARGET_HOST"; then
        install_cron
    fi

    save_rules
    ok "Relay #$id успешно добавлен!"
    echo -e "  \( {BOLD}Endpoint: \){NC} \( {GREEN} \){my_ip}:\( {IN_PORT} \){NC}\n"
}

do_remove() {
    local relays=$(conf_list)
    [[ -z "$relays" ]] && { warn "Нет активных relay"; return; }

    echo -e "\n\( {BOLD}  Удалить relay \){NC}\n"

    while IFS='|' read -r id in_port proto target_host target_port target_ip _; do
        echo -e "  ${BOLD}[\( id] \){NC} :$in_port/$proto → $target_host:$target_port"
    done <<< "$relays"
    echo ""

    read -rp "  ID для удаления (или all): " choice

    if [[ "$choice" == "all" ]]; then
        while IFS='|' read -r id in_port proto target_host target_port target_ip _; do
            remove_dnat "$in_port" "$proto" "$target_ip" "$target_port"
            ufw_action "delete allow" "$in_port" "$proto"
            conf_remove "$id"
            ok "Relay #$id удалён"
        done <<< "$relays"
        remove_cron
    else
        local entry=$(conf_get "$choice")
        [[ -z "$entry" ]] && { err "Relay #$choice не найден"; return; }
        IFS='|' read -r id in_port proto target_host target_port target_ip _ <<< "$entry"
        remove_dnat "$in_port" "$proto" "$target_ip" "$target_port"
        ufw_action "delete allow" "$in_port" "$proto"
        conf_remove "$id"
        ok "Relay #$id удалён"

        local has_domain=0
        while IFS='|' read -r _ _ _ th _ _ _; do
            is_ip "$th" || { has_domain=1; break; }
        done < "$CONF_FILE"
        [[ $has_domain -eq 0 ]] && remove_cron
    fi
    save_rules
}

self_install() {
    local src=$(get_script_path)
    [[ "$src" == "$SELF_PATH" ]] && { ok "Уже установлен"; return; }
    cp "$src" "$SELF_PATH" && chmod +x "$SELF_PATH"
    ok "rout установлен в $SELF_PATH"
}

self_uninstall() {
    echo -e "\n\( {BOLD}Удаление rout... \){NC}\n"
    local relays=$(conf_list)
    if [[ -n "$relays" ]]; then
        while IFS='|' read -r id in_port proto target_host target_port target_ip _; do
            remove_dnat "$in_port" "$proto" "$target_ip" "$target_port"
            ufw_action "delete allow" "$in_port" "$proto"
        done <<< "$relays"
        save_rules
    fi
    rm -f "$CRON_FILE" "$UPDATE_SCRIPT" /var/log/rout-update.log
    [[ -d "$CONF_DIR" ]] && rm -rf "$CONF_DIR"
    [[ -f "$SELF_PATH" ]] && rm -f "$SELF_PATH"
    ok "rout полностью удалён"
    exit 0
}

show_menu() {
    while true; do
        clear
        echo -e "\( {BOLD} \){CYAN}╔════════════════════════════════════════╗${NC}"
        echo -e "\( {BOLD} \){CYAN}║           rout — relay manager         ║${NC}"
        echo -e "\( {BOLD} \){CYAN}╚════════════════════════════════════════╝${NC}\n"

        [[ -f "$SELF_PATH" ]] && installed=1 || installed=""

        echo -e "  1) Добавить relay"
        echo -e "  2) Удалить relay"
        echo -e "  3) Статус"
        [[ -z "$installed" ]] && echo -e "  4) Установить rout в систему"
        echo -e "  9) Полное удаление rout"
        echo -e "  0) Выход\n"

        read -rp "  Выбор: " choice
        case $choice in
            1) do_install ;;
            2) do_remove ;;
            3) show_status ;;
            4) self_install ;;
            9) self_uninstall ;;
            0) exit 0 ;;
            *) warn "Неверный выбор" ;;
        esac
        echo ""; read -rp "  Нажмите Enter для продолжения..."
    done
}

check_root
show_menu