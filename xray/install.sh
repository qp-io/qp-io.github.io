#!/bin/bash
# Reality-EZPZ Multi-Instance Manager
# Каждый инстанс — независимый VPN-сервер на своём порту с отдельным ботом
# Данные: /opt/reality-ezpz-instances/<name>/
# Скрипты: /usr/local/bin/reality-ezpz-<name>.sh
# Команды: vless-<name>

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

SCRIPT_URL="https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh"
INSTANCES_DIR="/opt/reality-ezpz-instances"
BIN_DIR="/usr/local/bin"

# ─── Вывод ────────────────────────────────────────────────────────────────────

p() { echo -e "${1}${2}${NC}"; }

header() {
    clear
    echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${CYAN}     Reality-EZPZ · Multi-Instance Manager        ${NC}"
    echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════${NC}"
    echo ""
}

# ─── Работа с инстансами ──────────────────────────────────────────────────────

list_instances() {
    local arr=()
    if [[ -d "$INSTANCES_DIR" ]]; then
        while IFS= read -r -d '' d; do
            [[ -f "$d/config" ]] && arr+=("$(basename "$d")")
        done < <(find "$INSTANCES_DIR" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | sort -z)
    fi
    echo "${arr[@]:-}"
}

meta_get() {
    local file="$INSTANCES_DIR/$1/.meta"
    [[ -f "$file" ]] && grep "^$2=" "$file" 2>/dev/null | cut -d= -f2- || echo ""
}

meta_write() {
    local dir="$INSTANCES_DIR/$1"
    mkdir -p "$dir"
    cat > "$dir/.meta" << EOF
name=$2
port=$3
core=$4
tgbot=$5
created=$(date '+%Y-%m-%d %H:%M')
EOF
}

instance_status() {
    # Возвращает "running" или "stopped"
    local name=$1
    local compose="$INSTANCES_DIR/$name/docker-compose.yml"
    if [[ -f "$compose" ]]; then
        local proj="reality-${name}"
        if docker compose -p "$proj" --project-directory "$INSTANCES_DIR/$name" ps -q 2>/dev/null | grep -q .; then
            echo "running"; return
        fi
    fi
    echo "stopped"
}

# ─── Валидация ────────────────────────────────────────────────────────────────

validate_name() {
    # Только буквы, цифры, дефис. Без спецсимволов, длина 2-24.
    # Docker compose_project = reality-<name> — тоже должно быть допустимым именем проекта.
    [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9-]{1,23}$ ]]
}

validate_port() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 ))
}

port_free() {
    local port=$1
    # Проверяем системные порты
    if ss -tlnup 2>/dev/null | grep -q ":${port}[[:space:]]"; then
        return 1
    fi
    # Проверяем порты других инстансов
    for inst in $(list_instances); do
        [[ "$(meta_get "$inst" port)" == "$port" ]] && return 1
    done
    return 0
}

validate_tg_token() {
    local token=$1
    [[ "$token" =~ ^[0-9]{8,10}:[a-zA-Z0-9_-]{35}$ ]] || return 1
    curl -sSfL -m 5 "https://api.telegram.org/bot${token}/getMe" >/dev/null 2>&1
}

validate_tg_admins() {
    [[ "$1" =~ ^[a-zA-Z][a-zA-Z0-9_]{4,31}(,[a-zA-Z][a-zA-Z0-9_]{4,31})*$ ]]
}

# ─── Запуск скрипта инстанса ──────────────────────────────────────────────────

run_script() {
    # run_script <name> [args...]
    # Устанавливает REALITY_CONFIG_PATH и запускает скрипт инстанса
    local name=$1
    shift
    local script="$BIN_DIR/reality-ezpz-${name}.sh"
    if [[ ! -f "$script" ]]; then
        p "$RED" "  ❌ Скрипт не найден: $script"
        read -rp "  Enter..." _; return 1
    fi
    REALITY_CONFIG_PATH="$INSTANCES_DIR/$name" bash "$script" "$@"
}

# ─── Главное меню ─────────────────────────────────────────────────────────────

main_menu() {
    while true; do
        header
        local instances=( $(list_instances) )
        local count=${#instances[@]}

        if (( count == 0 )); then
            p "$DIM" "  Нет установленных инстансов."
            echo ""
        else
            printf "  ${BOLD}%-20s %-7s %-12s %-6s %-10s${NC}\n" \
                "Имя" "Порт" "Core" "Бот" "Статус"
            echo -e "  ${DIM}──────────────────────────────────────────────────${NC}"
            for name in "${instances[@]}"; do
                local port=$(meta_get "$name" port)
                local core=$(meta_get "$name" core)
                local tgbot=$(meta_get "$name" tgbot)
                local st=$(instance_status "$name")
                local st_color
                [[ "$st" == "running" ]] && st_color="${GREEN}▶ работает${NC}" || st_color="${RED}■ остановлен${NC}"
                printf "  ${CYAN}%-20s${NC} %-7s %-12s %-6s " "$name" "$port" "$core" "$tgbot"
                echo -e "$st_color"
            done
            echo ""
        fi

        echo -e "  ${GREEN}[N]${NC} Установить новый инстанс"
        (( count > 0 )) && echo -e "  ${YELLOW}[M]${NC} Управление инстансом"
        echo -e "  ${RED}[Q]${NC} Выход"
        echo ""
        read -rp "  Выбор: " choice

        case "${choice,,}" in
            n) install_wizard ;;
            m) (( count > 0 )) && select_instance || { p "$RED" "  Нет инстансов."; sleep 1; } ;;
            q) echo ""; exit 0 ;;
            *) p "$RED" "  ❌ Неверный выбор."; sleep 1 ;;
        esac
    done
}

# ─── Выбор инстанса ───────────────────────────────────────────────────────────

select_instance() {
    header
    local instances=( $(list_instances) )
    p "$BLUE" "  Выберите инстанс:"
    echo ""
    local idx=1
    for name in "${instances[@]}"; do
        printf "  ${CYAN}%2d)${NC} %-20s  порт: %s\n" "$idx" "$name" "$(meta_get "$name" port)"
        (( idx++ ))
    done
    echo ""
    read -rp "  Номер (Enter = отмена): " sel
    [[ -z "$sel" ]] && return
    if ! [[ "$sel" =~ ^[0-9]+$ ]] || (( sel < 1 || sel > ${#instances[@]} )); then
        p "$RED" "  ❌ Неверный выбор."; sleep 1; return
    fi
    instance_menu "${instances[$((sel-1))]}"
}

# ─── Меню инстанса ────────────────────────────────────────────────────────────

instance_menu() {
    local name=$1
    while true; do
        header
        local port=$(meta_get "$name" port)
        local core=$(meta_get "$name" core)
        local tgbot=$(meta_get "$name" tgbot)
        local created=$(meta_get "$name" created)
        local st=$(instance_status "$name")
        local st_color
        [[ "$st" == "running" ]] && st_color="${GREEN}▶ работает${NC}" || st_color="${RED}■ остановлен${NC}"

        echo -e "  ${BOLD}${CYAN}Инстанс: $name${NC}"
        printf "  Порт: %-8s Core: %-12s Бот: %s\n" "$port" "$core" "$tgbot"
        printf "  Создан: %s    Статус: " "$created"; echo -e "$st_color"
        echo ""
        echo -e "  ${CYAN}1)${NC} Открыть меню настроек"
        echo -e "  ${CYAN}2)${NC} Конфигурация сервера"
        echo -e "  ${CYAN}3)${NC} Список пользователей"
        echo -e "  ${CYAN}4)${NC} Перезапустить"
        echo -e "  ${CYAN}5)${NC} Обновить скрипт"
        echo -e "  ${RED}6)${NC} Удалить инстанс"
        echo -e "  ${YELLOW}0)${NC} ← Назад"
        echo ""
        read -rp "  Выбор: " action

        case "$action" in
            1) run_script "$name" --menu ;;
            2) run_script "$name" --show-server-config; read -rp $'\n  Enter...' _ ;;
            3) run_script "$name" --list-users; read -rp $'\n  Enter...' _ ;;
            4) do_restart "$name" ;;
            5) do_update "$name" ;;
            6) do_delete "$name" && return ;;
            0) return ;;
            *) p "$RED" "  ❌ Неверный выбор."; sleep 1 ;;
        esac
    done
}

# ─── Перезапуск ───────────────────────────────────────────────────────────────

do_restart() {
    local name=$1
    header
    p "$CYAN" "  ⏳ Перезапускаю инстанс '$name'..."
    run_script "$name" --restart
    p "$GREEN" "  ✅ Перезапущен."
    read -rp "  Enter..." _
}

# ─── Обновление скрипта ───────────────────────────────────────────────────────

do_update() {
    local name=$1
    header
    p "$BLUE" "  🔄 Обновляю скрипт инстанса '$name'..."
    local script="$BIN_DIR/reality-ezpz-${name}.sh"
    local bak="${script}.bak.$(date +%s)"
    cp "$script" "$bak"
    p "$DIM" "  Резервная копия: $bak"
    if curl -fsSL --retry 3 -m 30 -o "${script}.new" "$SCRIPT_URL"; then
        mv "${script}.new" "$script"
        chmod +x "$script"
        p "$GREEN" "  ✅ Скрипт обновлён. Применяю конфигурацию..."
        REALITY_CONFIG_PATH="$INSTANCES_DIR/$name" bash "$script"
        p "$GREEN" "  ✅ Инстанс '$name' обновлён."
    else
        p "$RED" "  ❌ Ошибка загрузки. Восстанавливаю резервную копию."
        cp "$bak" "$script"
    fi
    read -rp "  Enter..." _
}

# ─── Удаление инстанса ────────────────────────────────────────────────────────

do_delete() {
    local name=$1
    header
    p "$RED$BOLD" "  ⚠️  Удаление инстанса '$name'"
    echo ""
    p "$YELLOW" "  Будет удалено:"
    echo "    • Все данные и пользователи: $INSTANCES_DIR/$name"
    echo "    • Docker-контейнеры проектов: reality-${name}, tgbot-${name}"
    echo "    • Скрипт: $BIN_DIR/reality-ezpz-${name}.sh"
    echo "    • Команда: vless-${name}"
    echo ""
    read -rp "  Введите имя инстанса для подтверждения: " confirm
    if [[ "$confirm" != "$name" ]]; then
        p "$YELLOW" "  Отменено."; read -rp "  Enter..." _; return 1
    fi

    # Останавливаем docker compose проекты
    p "$CYAN" "  Останавливаю контейнеры..."
    local compose="$INSTANCES_DIR/$name/docker-compose.yml"
    local tgbot_compose="$INSTANCES_DIR/$name/tgbot/docker-compose.yml"
    if docker compose >/dev/null 2>&1; then
        [[ -f "$compose" ]]       && docker compose -p "reality-${name}" --project-directory "$INSTANCES_DIR/$name"       down --volumes --remove-orphans --timeout 5 2>/dev/null || true
        [[ -f "$tgbot_compose" ]] && docker compose -p "tgbot-${name}"   --project-directory "$INSTANCES_DIR/$name/tgbot" down --volumes --remove-orphans --timeout 5 2>/dev/null || true
    fi

    # Удаляем файлы
    rm -rf "$INSTANCES_DIR/$name"
    rm -f  "$BIN_DIR/reality-ezpz-${name}.sh"
    rm -f  "$BIN_DIR/vless-${name}"
    rm -f  "/usr/bin/vless-${name}"

    p "$GREEN" "  ✅ Инстанс '$name' удалён."
    read -rp "  Enter..." _
    return 0
}

# ─── Мастер установки нового инстанса ────────────────────────────────────────

install_wizard() {
    header
    p "$BOLD$BLUE" "  ┌─ Новый инстанс ────────────────────────────────┐"
    echo ""

    # ── 1. Имя ──
    local name=""
    while true; do
        read -rp "  Имя инстанса (буквы/цифры/дефис, 2-24 символа): " name
        if ! validate_name "$name"; then
            p "$RED" "  ❌ Только a-z A-Z 0-9 - (2-24 символа)."; continue
        fi
        if [[ -d "$INSTANCES_DIR/$name" ]]; then
            p "$RED" "  ❌ Инстанс '$name' уже существует."; continue
        fi
        p "$GREEN" "  ✅ Имя: $name"; break
    done
    echo ""

    # ── 2. Порт ──
    local port=""
    while true; do
        read -rp "  Порт (1-65535): " port
        if ! validate_port "$port"; then
            p "$RED" "  ❌ Некорректный порт."; continue
        fi
        if ! port_free "$port"; then
            p "$YELLOW" "  ⚠️  Порт $port уже используется. Другой? (y/n):"
            read -rp "  " yn
            [[ "${yn,,}" == "y" ]] && continue
        fi
        p "$GREEN" "  ✅ Порт: $port"; break
    done
    echo ""

    # ── 3. Core ──
    local core=""
    p "$BLUE" "  Core:"
    echo "    1) xray      — рекомендуется (tcp/http/grpc/ws/xhttp/reality)"
    echo "    2) sing-box  — tuic / hysteria2 / shadowtls"
    echo ""
    while true; do
        read -rp "  Выбор (1/2): " cc
        case "$cc" in
            1) core="xray";     p "$GREEN" "  ✅ xray";     break ;;
            2) core="sing-box"; p "$GREEN" "  ✅ sing-box"; break ;;
            *) p "$RED" "  ❌ Введите 1 или 2." ;;
        esac
    done
    echo ""

    # ── 4. Telegram-бот ──
    local use_bot=false tg_token="" tg_admins=""
    p "$BLUE" "  Telegram-бот для управления этим инстансом?"
    echo "    1) Да — включить бота"
    echo "    2) Нет"
    echo ""
    while true; do
        read -rp "  Выбор (1/2): " bc
        case "$bc" in
            1) use_bot=true;  break ;;
            2) use_bot=false; break ;;
            *) p "$RED" "  ❌ Введите 1 или 2." ;;
        esac
    done
    echo ""

    if [[ "$use_bot" == true ]]; then
        p "$CYAN" "  Откройте @BotFather → /newbot → скопируйте токен"
        echo ""
        while true; do
            read -rp "  Токен бота: " tg_token
            if validate_tg_token "$tg_token"; then
                p "$GREEN" "  ✅ Токен действителен."; break
            else
                p "$RED" "  ❌ Неверный токен или бот недоступен. Повторите."
            fi
        done
        echo ""
        while true; do
            p "$CYAN" "  Логины админов (без @, через запятую, пример: admin1,admin2):"
            read -rp "  " tg_admins
            if validate_tg_admins "$tg_admins"; then
                p "$GREEN" "  ✅ Логины корректны."; break
            else
                p "$RED" "  ❌ Неверный формат."
            fi
        done
        echo ""
    fi

    # ── 5. Подтверждение ──
    header
    p "$BOLD" "  Конфигурация нового инстанса:"
    echo ""
    printf "  ${CYAN}%-20s${NC} %s\n" "Имя:"         "$name"
    printf "  ${CYAN}%-20s${NC} %s\n" "Порт:"        "$port"
    printf "  ${CYAN}%-20s${NC} %s\n" "Core:"        "$core"
    printf "  ${CYAN}%-20s${NC} %s\n" "Telegram-бот:" "$([ "$use_bot" = true ] && echo "включён" || echo "отключён")"
    [[ "$use_bot" == true ]] && {
        printf "  ${CYAN}%-20s${NC} %s\n" "Токен:" "${tg_token:0:10}***"
        printf "  ${CYAN}%-20s${NC} %s\n" "Админы:" "$tg_admins"
    }
    printf "  ${DIM}%-20s${NC} %s\n" "Docker compose:" "reality-${name}"
    printf "  ${DIM}%-20s${NC} %s\n" "Данные:" "$INSTANCES_DIR/$name"
    printf "  ${DIM}%-20s${NC} %s\n" "Команда:" "vless-${name}"
    echo ""
    read -rp "  Установить? (y/n): " confirm
    [[ "${confirm,,}" != "y" ]] && { p "$YELLOW" "  Отменено."; sleep 1; return; }

    do_install "$name" "$port" "$core" "$use_bot" "$tg_token" "$tg_admins"
}

# ─── Установка инстанса ───────────────────────────────────────────────────────

do_install() {
    local name=$1 port=$2 core=$3 use_bot=$4 tg_token=$5 tg_admins=$6
    local inst_dir="$INSTANCES_DIR/$name"
    local script="$BIN_DIR/reality-ezpz-${name}.sh"

    header
    p "$BLUE" "  🚀 Устанавливаю инстанс '${name}'..."
    echo ""

    # Создаём директорию инстанса
    mkdir -p "$inst_dir"

    # Сохраняем мета-данные (до запуска скрипта)
    meta_write "$name" "$name" "$port" "$core" "$([ "$use_bot" = true ] && echo ON || echo OFF)"

    # Загружаем скрипт
    p "$CYAN" "  ⬇ Загружаю reality-ezpz.sh..."
    if ! curl -fsSL --retry 3 -m 60 -o "$script" "$SCRIPT_URL"; then
        p "$RED" "  ❌ Ошибка загрузки скрипта!"
        rm -rf "$inst_dir"
        read -rp "  Enter..." _; return 1
    fi
    chmod +x "$script"
    p "$GREEN" "  ✅ Скрипт: $script"
    echo ""

    # Создаём команду vless-<name>
    create_alias "$name"

    # Формируем аргументы запуска
    # REALITY_CONFIG_PATH → скрипт сам поставит:
    #   config_path  = $REALITY_CONFIG_PATH   = $inst_dir
    #   instance_name = basename($inst_dir)   = $name
    #   compose_project = reality-$name
    #   tgbot_project   = tgbot-$name
    #   subnet_main/tgbot = детерминированно из $name (без конфликтов)
    local cmd_args="--core=$core --port=$port"
    if [[ "$use_bot" == true ]]; then
        cmd_args="$cmd_args --enable-tgbot=true --tgbot-token=$tg_token --tgbot-admins=$tg_admins"
    else
        cmd_args="$cmd_args --enable-tgbot=false"
    fi

    p "$CYAN" "  ▶ Запускаю настройку..."
    echo ""
    sleep 1

    if REALITY_CONFIG_PATH="$inst_dir" bash "$script" $cmd_args; then
        echo ""
        header
        p "$GREEN" "  🎉 Инстанс '${name}' успешно установлен!"
        echo ""
        p "$CYAN"  "  Управление:"
        printf "  ${NC}  %-30s %s\n" "vless-${name} -m"      "— меню настроек"
        printf "  ${NC}  %-30s %s\n" "vless-${name} -r"      "— перезапуск"
        printf "  ${NC}  %-30s %s\n" "vless-${name} users"   "— список пользователей"
        printf "  ${NC}  %-30s %s\n" "vless-${name} add U"   "— добавить пользователя"
        printf "  ${NC}  %-30s %s\n" "vless-${name} show U"  "— QR-код пользователя"
        printf "  ${NC}  %-30s %s\n" "vless-${name} -u"      "— удаление"
        echo ""
        [[ "$use_bot" == true ]] && p "$YELLOW" "  📱 Telegram-бот активен. Напишите боту /start"
    else
        p "$RED" "  ❌ Установка завершилась с ошибкой. Проверьте вывод выше."
    fi
    read -rp $'\n  Нажмите Enter для продолжения...' _
}

# ─── Алиас vless-<name> ───────────────────────────────────────────────────────

create_alias() {
    local name=$1
    local alias_path="$BIN_DIR/vless-${name}"
    local inst_dir="$INSTANCES_DIR/$name"
    local script="$BIN_DIR/reality-ezpz-${name}.sh"

    cat > "$alias_path" << ALIASEOF
#!/bin/bash
# Управление инстансом: $name
# Данные: $inst_dir
export REALITY_CONFIG_PATH="$inst_dir"
SCRIPT="$script"

case "\${1:-}" in
    -m|--menu)      exec bash "\$SCRIPT" --menu ;;
    -r|--restart)   exec bash "\$SCRIPT" --restart ;;
    -u|--uninstall) exec bash "\$SCRIPT" --uninstall ;;
    config)         exec bash "\$SCRIPT" --show-server-config ;;
    users)          exec bash "\$SCRIPT" --list-users ;;
    add)            [[ -z "\${2:-}" ]] && { echo "Укажите имя: vless-${name} add <user>"; exit 1; }
                    exec bash "\$SCRIPT" --add-user "\$2" ;;
    del)            [[ -z "\${2:-}" ]] && { echo "Укажите имя: vless-${name} del <user>"; exit 1; }
                    exec bash "\$SCRIPT" --delete-user "\$2" ;;
    show)           [[ -z "\${2:-}" ]] && { echo "Укажите имя: vless-${name} show <user>"; exit 1; }
                    exec bash "\$SCRIPT" --show-user "\$2" ;;
    backup)         exec bash "\$SCRIPT" --backup ;;
    restore)        [[ -z "\${2:-}" ]] && { echo "Укажите файл: vless-${name} restore <file>"; exit 1; }
                    exec bash "\$SCRIPT" --restore "\$2" ;;
    "")
        echo ""
        echo "  Инстанс : $name"
        echo "  Данные  : $inst_dir"
        echo "  Проект  : reality-${name} / tgbot-${name}"
        echo ""
        echo "  Использование:"
        echo "    vless-${name} -m         # Меню настроек"
        echo "    vless-${name} -r         # Перезапуск"
        echo "    vless-${name} users      # Список пользователей"
        echo "    vless-${name} add <u>    # Добавить пользователя"
        echo "    vless-${name} show <u>   # QR-код"
        echo "    vless-${name} config     # Конфигурация сервера"
        echo "    vless-${name} backup     # Бэкап"
        echo "    vless-${name} -u         # Удаление"
        echo ""
        ;;
    *)  exec bash "\$SCRIPT" "\$@" ;;
esac
ALIASEOF
    chmod +x "$alias_path"
    ln -sf "$alias_path" "/usr/bin/vless-${name}" 2>/dev/null || true
    export PATH="$BIN_DIR:$PATH"
    p "$GREEN" "  ✅ Команда: vless-${name}"
}

# ─── Миграция одиночной установки (обратная совместимость) ───────────────────

try_migrate_legacy() {
    # Если /opt/reality-ezpz существует и ещё НЕ является директорией инстанса
    if [[ -f "/opt/reality-ezpz/config" && ! -L "/opt/reality-ezpz" ]]; then
        # Проверяем — не мигрировали ли уже
        if [[ -d "$INSTANCES_DIR/default" ]]; then return; fi
        header
        p "$YELLOW" "  Обнаружена существующая установка в /opt/reality-ezpz"
        p "$CYAN"   "  Перенести её в менеджер как инстанс 'default'?"
        echo ""
        read -rp "  (y/n): " yn
        [[ "${yn,,}" != "y" ]] && return

        mkdir -p "$INSTANCES_DIR"
        # Переносим данные
        cp -a /opt/reality-ezpz "$INSTANCES_DIR/default"
        local port=$(grep '^port=' /opt/reality-ezpz/config 2>/dev/null | cut -d= -f2 || echo "443")
        local core=$(grep '^core=' /opt/reality-ezpz/config 2>/dev/null | cut -d= -f2 || echo "xray")
        local tgbot=$(grep '^tgbot=' /opt/reality-ezpz/config 2>/dev/null | cut -d= -f2 || echo "OFF")
        meta_write "default" "default" "$port" "$core" "$tgbot"

        # Скрипт
        if [[ -f "$BIN_DIR/reality-ezpz.sh" ]]; then
            cp "$BIN_DIR/reality-ezpz.sh" "$BIN_DIR/reality-ezpz-default.sh"
            chmod +x "$BIN_DIR/reality-ezpz-default.sh"
        else
            p "$CYAN" "  Загружаю скрипт..."
            curl -fsSL --retry 3 -m 30 -o "$BIN_DIR/reality-ezpz-default.sh" "$SCRIPT_URL"
            chmod +x "$BIN_DIR/reality-ezpz-default.sh"
        fi
        create_alias "default"
        p "$GREEN" "  ✅ Мигрировано как инстанс 'default'"
        p "$CYAN"  "  Команда: vless-default"
        p "$YELLOW" "  ⚠️  Оригинальный /opt/reality-ezpz сохранён (не удалён)"
        sleep 2
    fi
}

# ─── Точка входа ──────────────────────────────────────────────────────────────

# При запуске через bash <(curl ...) stdin занят pipe'ом — перепривязываем к терминалу
exec < /dev/tty

if [[ "${EUID}" -ne 0 ]]; then
    p "$RED" "❌ Требуются права root. Запустите: sudo bash <(curl -sL URL)"
    exit 1
fi

mkdir -p "$INSTANCES_DIR" "$BIN_DIR"
export PATH="$BIN_DIR:$PATH"

# Сохраняем скрипт в /usr/local/bin/install.sh для повторного использования
INSTALL_SCRIPT_URL="https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/install.sh"
if [[ ! -f "$BIN_DIR/install.sh" ]] || [[ "$(realpath "${BASH_SOURCE[0]:-$0}")" != "$BIN_DIR/install.sh" ]]; then
    if curl -fsSL --retry 3 -m 30 -o "$BIN_DIR/install.sh.tmp" "$INSTALL_SCRIPT_URL" 2>/dev/null; then
        mv "$BIN_DIR/install.sh.tmp" "$BIN_DIR/install.sh"
        chmod +x "$BIN_DIR/install.sh"
    else
        # Если curl не удался — копируем себя (работает когда запущен как файл)
        [[ -f "${BASH_SOURCE[0]:-}" ]] && cp "${BASH_SOURCE[0]}" "$BIN_DIR/install.sh" && chmod +x "$BIN_DIR/install.sh" || true
    fi
fi

# Создаём команду 'vless' как ярлык менеджера
cat > "$BIN_DIR/vless" << 'VLESSEOF'
#!/bin/bash
exec bash /usr/local/bin/install.sh "$@"
VLESSEOF
chmod +x "$BIN_DIR/vless"
ln -sf "$BIN_DIR/vless" /usr/bin/vless 2>/dev/null || true

try_migrate_legacy
main_menu
