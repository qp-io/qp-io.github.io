#!/bin/bash
# Reality-EZPZ Multi-Instance Manager
# Управление несколькими независимыми инстансами на одном сервере

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_URL="https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh"
INSTANCES_DIR="/opt/reality-ezpz-instances"
BIN_DIR="/usr/local/bin"

p()  { echo -e "${1}${2}${NC}"; }
ph() {
    clear
    echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${CYAN}       Reality-EZPZ  ·  Multi-Instance Manager    ${NC}"
    echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════${NC}"
    echo ""
}

# ─── Работа с инстансами ──────────────────────────────────────────────────────

# Список всех установленных инстансов
list_instances() {
    local instances=()
    if [[ -d "$INSTANCES_DIR" ]]; then
        for d in "$INSTANCES_DIR"/*/; do
            [[ -f "$d/config" ]] && instances+=("$(basename "$d")")
        done
    fi
    echo "${instances[@]:-}"
}

# Мета-файл инстанса: name, port, core, tgbot, created_at
instance_meta() {
    local name=$1
    local meta="$INSTANCES_DIR/$name/.meta"
    [[ -f "$meta" ]] && cat "$meta" || echo ""
}

meta_get() {
    local name=$1 key=$2
    instance_meta "$name" | grep "^$key=" | cut -d= -f2-
}

meta_set() {
    local name=$1 key=$2 val=$3
    local meta="$INSTANCES_DIR/$name/.meta"
    mkdir -p "$INSTANCES_DIR/$name"
    if grep -q "^$key=" "$meta" 2>/dev/null; then
        sed -i "s|^$key=.*|$key=$val|" "$meta"
    else
        echo "$key=$val" >> "$meta"
    fi
}

# Проверяет, запущен ли docker compose инстанса
instance_running() {
    local name=$1
    local compose="$INSTANCES_DIR/$name/docker-compose.yml"
    [[ -f "$compose" ]] && docker compose -f "$compose" ps --quiet 2>/dev/null | grep -q . && return 0 || return 1
}

# ─── Валидация ────────────────────────────────────────────────────────────────

validate_name() {
    local name=$1
    [[ "$name" =~ ^[a-zA-Z0-9_-]{2,24}$ ]]
}

validate_port() {
    local port=$1
    [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 ))
}

port_in_use() {
    local port=$1
    # Проверяем и системные порты и порты других инстансов
    ss -tlnup 2>/dev/null | grep -q ":$port " && return 0
    for inst in $(list_instances); do
        [[ "$(meta_get "$inst" port)" == "$port" ]] && return 0
    done
    return 1
}

validate_tg_token() {
    local token=$1
    [[ "$token" =~ ^[0-9]{8,10}:[a-zA-Z0-9_-]{35}$ ]] || return 1
    curl -sSfL -m 5 "https://api.telegram.org/bot${token}/getMe" >/dev/null 2>&1
}

validate_tg_admins() {
    local admins=$1
    [[ "$admins" =~ ^[a-zA-Z][a-zA-Z0-9_]{4,31}(,[a-zA-Z][a-zA-Z0-9_]{4,31})*$ ]]
}

# ─── Главное меню ─────────────────────────────────────────────────────────────

main_menu() {
    while true; do
        ph
        local instances=( $(list_instances) )
        local count=${#instances[@]}

        if (( count == 0 )); then
            p "$YELLOW" "  Нет установленных инстансов."
        else
            p "$BOLD" "  Установленные инстансы:"
            echo ""
            local idx=1
            for name in "${instances[@]}"; do
                local port=$(meta_get "$name" port)
                local core=$(meta_get "$name" core)
                local tgbot=$(meta_get "$name" tgbot)
                local status
                instance_running "$name" && status="${GREEN}▶ запущен${NC}" || status="${RED}■ остановлен${NC}"
                printf "  ${CYAN}%2d)${NC} ${BOLD}%-18s${NC} порт:%-6s core:%-10s бот:%-4s  %b\n" \
                    "$idx" "$name" "$port" "$core" "$tgbot" "$status"
                (( idx++ ))
            done
        fi

        echo ""
        echo -e "  ${GREEN}N)${NC} Установить новый инстанс"
        (( count > 0 )) && echo -e "  ${YELLOW}M)${NC} Управление инстансом"
        echo -e "  ${RED}Q)${NC} Выход"
        echo ""
        read -rp "  Выбор: " choice

        case "${choice,,}" in
            n) install_wizard ;;
            m) (( count > 0 )) && manage_menu || p "$RED" "Нет инстансов." ;;
            q) echo ""; exit 0 ;;
            *) p "$RED" "Неверный выбор." ; sleep 1 ;;
        esac
    done
}

# ─── Меню управления инстансом ────────────────────────────────────────────────

manage_menu() {
    ph
    local instances=( $(list_instances) )
    p "$BLUE" "  Выберите инстанс для управления:"
    echo ""
    local idx=1
    for name in "${instances[@]}"; do
        local port=$(meta_get "$name" port)
        printf "  ${CYAN}%2d)${NC} %s  (порт %s)\n" "$idx" "$name" "$port"
        (( idx++ ))
    done
    echo ""
    read -rp "  Номер (или Enter для отмены): " sel
    [[ -z "$sel" ]] && return
    if ! [[ "$sel" =~ ^[0-9]+$ ]] || (( sel < 1 || sel > ${#instances[@]} )); then
        p "$RED" "Неверный выбор." ; sleep 1 ; return
    fi
    local chosen="${instances[$((sel-1))]}"
    instance_actions "$chosen"
}

# ─── Действия с инстансом ─────────────────────────────────────────────────────

instance_actions() {
    local name=$1
    while true; do
        ph
        local port=$(meta_get "$name" port)
        local core=$(meta_get "$name" core)
        local tgbot=$(meta_get "$name" tgbot)
        local created=$(meta_get "$name" created_at)
        local status
        instance_running "$name" && status="${GREEN}▶ запущен${NC}" || status="${RED}■ остановлен${NC}"

        echo -e "  ${BOLD}Инстанс: ${CYAN}$name${NC}"
        echo -e "  Порт:    $port    Core: $core    Бот: $tgbot"
        echo -e "  Создан:  $created"
        printf "  Статус:  %b\n" "$status"
        echo ""
        echo -e "  ${CYAN}1)${NC} Открыть меню настроек (whiptail)"
        echo -e "  ${CYAN}2)${NC} Перезапустить"
        echo -e "  ${CYAN}3)${NC} Обновить скрипт и пересоздать"
        echo -e "  ${CYAN}4)${NC} Показать конфигурацию сервера"
        echo -e "  ${CYAN}5)${NC} Список пользователей"
        echo -e "  ${RED}6)${NC} Удалить инстанс"
        echo -e "  ${YELLOW}0)${NC} Назад"
        echo ""
        read -rp "  Выбор: " action

        case "$action" in
            1) run_instance "$name" "--menu" ;;
            2) restart_instance "$name" ;;
            3) update_instance "$name" ;;
            4) run_instance "$name" "--show-server-config" ;;
            5) run_instance "$name" "--list-users" ;;
            6) delete_instance "$name" && return ;;
            0) return ;;
            *) p "$RED" "Неверный выбор." ; sleep 1 ;;
        esac
    done
}

# ─── Запуск команды для конкретного инстанса ─────────────────────────────────

run_instance() {
    local name=$1
    local args="${2:-}"
    local script="$BIN_DIR/reality-ezpz-${name}.sh"
    local config_path="$INSTANCES_DIR/$name"

    if [[ ! -f "$script" ]]; then
        p "$RED" "Скрипт инстанса не найден: $script"
        read -rp "Нажмите Enter..." _; return 1
    fi

    # Для интерактивных команд (menu) запускаем напрямую
    if [[ "$args" == "--menu" ]]; then
        REALITY_CONFIG_PATH="$config_path" bash "$script" --menu
    else
        REALITY_CONFIG_PATH="$config_path" bash "$script" $args
        read -rp $'\nНажмите Enter для продолжения...' _
    fi
}

# ─── Мастер установки нового инстанса ────────────────────────────────────────

install_wizard() {
    ph
    p "$BOLD$BLUE" "  ┌─ Новый инстанс ──────────────────────────────┐"
    echo ""

    # 1. Имя инстанса
    local name=""
    while true; do
        read -rp "  Имя инстанса (2-24 символа, a-z 0-9 _ -): " name
        name="${name// /_}"
        if ! validate_name "$name"; then
            p "$RED" "  ❌ Недопустимое имя. Только a-z, A-Z, 0-9, _, - (2-24 символа)."
            continue
        fi
        if [[ -d "$INSTANCES_DIR/$name" ]]; then
            p "$RED" "  ❌ Инстанс '$name' уже существует."
            continue
        fi
        p "$GREEN" "  ✅ Имя: $name"
        break
    done
    echo ""

    # 2. Порт
    local port=""
    while true; do
        read -rp "  Порт сервера (443, 8443, и т.д.): " port
        if ! validate_port "$port"; then
            p "$RED" "  ❌ Порт должен быть числом от 1 до 65535."
            continue
        fi
        if port_in_use "$port"; then
            p "$YELLOW" "  ⚠️  Порт $port уже занят. Выбрать другой? (y/n)"
            read -rp "  " yn
            [[ "${yn,,}" == "y" ]] && continue
        fi
        p "$GREEN" "  ✅ Порт: $port"
        break
    done
    echo ""

    # 3. Core
    local core=""
    p "$BLUE" "  Core:"
    echo "    1) xray      (рекомендуется)"
    echo "    2) sing-box  (туик, hysteria2, shadowtls)"
    echo ""
    while true; do
        read -rp "  Выбор (1/2): " cc
        case "$cc" in
            1) core="xray"     ; p "$GREEN" "  ✅ Core: xray"     ; break ;;
            2) core="sing-box" ; p "$GREEN" "  ✅ Core: sing-box" ; break ;;
            *) p "$RED" "  ❌ Введите 1 или 2." ;;
        esac
    done
    echo ""

    # 4. Telegram бот
    local use_bot=false
    local tg_token="" tg_admins=""
    p "$BLUE" "  Telegram-бот для управления инстансом?"
    echo "    1) Да"
    echo "    2) Нет"
    echo ""
    while true; do
        read -rp "  Выбор (1/2): " bc
        case "$bc" in
            1) use_bot=true  ; break ;;
            2) use_bot=false ; break ;;
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
                p "$GREEN" "  ✅ Токен действителен."
                break
            else
                p "$RED" "  ❌ Неверный токен или бот недоступен."
            fi
        done
        echo ""
        while true; do
            read -rp "  Логины админов (без @, через запятую): " tg_admins
            if validate_tg_admins "$tg_admins"; then
                p "$GREEN" "  ✅ Логины корректны."
                break
            else
                p "$RED" "  ❌ Неверный формат. Пример: admin1,admin2"
            fi
        done
        echo ""
    fi

    # 5. Подтверждение
    ph
    p "$BOLD" "  Конфигурация нового инстанса:"
    echo ""
    p "$CYAN"   "    Имя:          $name"
    p "$CYAN"   "    Порт:         $port"
    p "$CYAN"   "    Core:         $core"
    p "$CYAN"   "    Telegram-бот: $([ "$use_bot" = true ] && echo "включён" || echo "отключён")"
    [[ "$use_bot" == true ]] && {
        p "$CYAN" "    Токен:        ${tg_token:0:10}***"
        p "$CYAN" "    Админы:       $tg_admins"
    }
    echo ""
    read -rp "  Установить? (y/n): " confirm
    [[ "${confirm,,}" != "y" ]] && p "$YELLOW" "  Отменено." && sleep 1 && return

    # 6. Установка
    do_install "$name" "$port" "$core" "$use_bot" "$tg_token" "$tg_admins"
}

# ─── Установка инстанса ───────────────────────────────────────────────────────

do_install() {
    local name=$1 port=$2 core=$3 use_bot=$4 tg_token=$5 tg_admins=$6

    ph
    p "$BLUE" "  🚀 Устанавливаю инстанс '$name'..."
    echo ""

    local config_path="$INSTANCES_DIR/$name"
    local script_path="$BIN_DIR/reality-ezpz-${name}.sh"

    # Создаём директорию инстанса
    mkdir -p "$config_path"

    # Загружаем скрипт
    p "$CYAN" "  Загружаю скрипт..."
    if ! curl -fsSL --retry 3 -m 30 -o "$script_path" "$SCRIPT_URL"; then
        p "$RED" "  ❌ Ошибка загрузки скрипта!"
        rmdir "$config_path" 2>/dev/null || true
        read -rp "  Enter..." _; return 1
    fi
    chmod +x "$script_path"
    p "$GREEN" "  ✅ Скрипт: $script_path"

    # Записываем мета-данные
    mkdir -p "$config_path"
    cat > "$config_path/.meta" << EOF
name=$name
port=$port
core=$core
tgbot=$([ "$use_bot" = true ] && echo "ON" || echo "OFF")
created_at=$(date '+%Y-%m-%d %H:%M')
EOF

    # Формируем команду запуска
    local cmd="REALITY_CONFIG_PATH=$config_path bash $script_path --core=$core --port=$port"
    [[ "$use_bot" == true ]] && \
        cmd="$cmd --enable-tgbot=true --tgbot-token=$tg_token --tgbot-admins=$tg_admins" || \
        cmd="$cmd --enable-tgbot=false"

    p "$CYAN" "  Запускаю скрипт настройки..."
    echo ""
    sleep 1

    # Запускаем
    eval "$cmd"
    local rc=$?

    if (( rc == 0 )); then
        # Создаём удобный алиас-команду
        setup_instance_alias "$name" "$config_path" "$script_path"
        echo ""
        ph
        p "$GREEN" "  🎉 Инстанс '$name' установлен!"
        echo ""
        p "$CYAN"  "    Команда управления: vless-${name}"
        p "$CYAN"  "    Меню:               vless-${name} -m"
        p "$CYAN"  "    Перезапуск:         vless-${name} -r"
        echo ""
    else
        p "$RED" "  ❌ Установка завершилась с ошибкой (код $rc)."
    fi
    read -rp "  Нажмите Enter для продолжения..." _
}

# ─── Создание алиаса для инстанса ─────────────────────────────────────────────

setup_instance_alias() {
    local name=$1 config_path=$2 script_path=$3
    local alias_path="$BIN_DIR/vless-${name}"

    cat > "$alias_path" << ALIASEOF
#!/bin/bash
SCRIPT="$script_path"
CONFIG_PATH="$config_path"

case "\${1:-}" in
    -m|--menu)     REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --menu ;;
    -r|--restart)  REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --restart ;;
    -u|--uninstall)REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --uninstall ;;
    config)        REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --show-server-config ;;
    users)         REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --list-users ;;
    add)           REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --add-user "\$2" ;;
    del)           REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --delete-user "\$2" ;;
    show)          REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --show-user "\$2" ;;
    backup)        REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" --backup ;;
    *)
        if [[ -n "\${1:-}" ]]; then
            REALITY_CONFIG_PATH="\$CONFIG_PATH" exec "\$SCRIPT" "\$@"
        else
            echo "Инстанс: $name  (порт: $(meta_get "$name" port)  core: $(meta_get "$name" core))"
            echo ""
            echo "  vless-${name} -m      # Меню"
            echo "  vless-${name} -r      # Перезапуск"
            echo "  vless-${name} users   # Пользователи"
            echo "  vless-${name} add U   # Добавить"
            echo "  vless-${name} show U  # QR-код"
        fi
        ;;
esac
ALIASEOF
    chmod +x "$alias_path"
    ln -sf "$alias_path" "/usr/bin/vless-${name}" 2>/dev/null || true
    export PATH="/usr/local/bin:$PATH"
    p "$GREEN" "  ✅ Команда: vless-${name}"
}

# ─── Перезапуск инстанса ──────────────────────────────────────────────────────

restart_instance() {
    local name=$1
    p "$CYAN" "  ⏳ Перезапускаю '$name'..."
    REALITY_CONFIG_PATH="$INSTANCES_DIR/$name" bash "$BIN_DIR/reality-ezpz-${name}.sh" --restart
    p "$GREEN" "  ✅ Перезапущен."
    read -rp "  Enter..." _
}

# ─── Обновление скрипта инстанса ──────────────────────────────────────────────

update_instance() {
    local name=$1
    local script_path="$BIN_DIR/reality-ezpz-${name}.sh"
    ph
    p "$BLUE" "  🔄 Обновляю скрипт инстанса '$name'..."

    local backup="${script_path}.bak.$(date +%s)"
    cp "$script_path" "$backup"
    p "$CYAN" "  Резервная копия: $backup"

    if curl -fsSL --retry 3 -m 30 -o "${script_path}.new" "$SCRIPT_URL"; then
        mv "${script_path}.new" "$script_path"
        chmod +x "$script_path"
        p "$GREEN" "  ✅ Скрипт обновлён. Пересоздаю конфигурацию..."
        REALITY_CONFIG_PATH="$INSTANCES_DIR/$name" bash "$script_path"
        p "$GREEN" "  ✅ Инстанс '$name' обновлён."
    else
        p "$RED" "  ❌ Ошибка загрузки. Восстанавливаю резервную копию..."
        cp "$backup" "$script_path"
    fi
    read -rp "  Enter..." _
}

# ─── Удаление инстанса ────────────────────────────────────────────────────────

delete_instance() {
    local name=$1
    ph
    p "$RED$BOLD" "  ⚠️  Удаление инстанса '$name'"
    echo ""
    p "$YELLOW" "  Это удалит:"
    echo "    • Все конфиги и пользователей ($INSTANCES_DIR/$name)"
    echo "    • Docker-контейнеры инстанса"
    echo "    • Скрипт $BIN_DIR/reality-ezpz-${name}.sh"
    echo "    • Команду vless-${name}"
    echo ""
    read -rp "  Введите имя инстанса для подтверждения: " confirm_name
    if [[ "$confirm_name" != "$name" ]]; then
        p "$YELLOW" "  Отменено."
        read -rp "  Enter..." _; return 1
    fi

    # Останавливаем docker compose
    local compose="$INSTANCES_DIR/$name/docker-compose.yml"
    if [[ -f "$compose" ]]; then
        p "$CYAN" "  Останавливаю контейнеры..."
        docker compose -f "$compose" down --volumes --remove-orphans 2>/dev/null || true
    fi

    # Telegram-бот compose
    local tgbot_compose="$INSTANCES_DIR/$name/tgbot/docker-compose.yml"
    if [[ -f "$tgbot_compose" ]]; then
        docker compose -f "$tgbot_compose" down --volumes --remove-orphans 2>/dev/null || true
    fi

    # Удаляем файлы
    rm -rf "$INSTANCES_DIR/$name"
    rm -f "$BIN_DIR/reality-ezpz-${name}.sh"
    rm -f "$BIN_DIR/vless-${name}"
    rm -f "/usr/bin/vless-${name}"

    p "$GREEN" "  ✅ Инстанс '$name' удалён."
    read -rp "  Enter..." _
}

# ─── Обратная совместимость: если нет инстансов и вызван без аргументов ───────

legacy_check() {
    # Если уже есть /opt/reality-ezpz (старая единственная установка)
    # предлагаем мигрировать
    if [[ -f "/opt/reality-ezpz/config" && ! -d "$INSTANCES_DIR" ]]; then
        ph
        p "$YELLOW" "  Обнаружена существующая установка в /opt/reality-ezpz"
        p "$CYAN"   "  Хотите перенести её под управление этого менеджера?"
        echo "    Имя инстанса: 'default'"
        echo ""
        read -rp "  Мигрировать? (y/n): " yn
        if [[ "${yn,,}" == "y" ]]; then
            mkdir -p "$INSTANCES_DIR"
            cp -a /opt/reality-ezpz "$INSTANCES_DIR/default"
            local port=$(grep '^port=' /opt/reality-ezpz/config 2>/dev/null | cut -d= -f2 || echo "443")
            local core=$(grep '^core=' /opt/reality-ezpz/config 2>/dev/null | cut -d= -f2 || echo "xray")
            local tgbot=$(grep '^tgbot=' /opt/reality-ezpz/config 2>/dev/null | cut -d= -f2 || echo "OFF")
            cat > "$INSTANCES_DIR/default/.meta" << EOF
name=default
port=$port
core=$core
tgbot=$tgbot
created_at=$(date '+%Y-%m-%d %H:%M') (мигрирован)
EOF
            # Копируем скрипт
            if [[ -f "$BIN_DIR/reality-ezpz.sh" ]]; then
                cp "$BIN_DIR/reality-ezpz.sh" "$BIN_DIR/reality-ezpz-default.sh"
            else
                curl -fsSL --retry 3 -m 30 -o "$BIN_DIR/reality-ezpz-default.sh" "$SCRIPT_URL"
                chmod +x "$BIN_DIR/reality-ezpz-default.sh"
            fi
            setup_instance_alias "default" "$INSTANCES_DIR/default" "$BIN_DIR/reality-ezpz-default.sh"
            p "$GREEN" "  ✅ Мигрировано как инстанс 'default'. Команда: vless-default"
            sleep 2
        fi
    fi
}

# ─── Точка входа ──────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    p "$RED" "❌ Требуются права root. Запустите: sudo $0"
    exit 1
fi

mkdir -p "$INSTANCES_DIR"
mkdir -p "$BIN_DIR"
export PATH="$BIN_DIR:$PATH"

# Также создаём/обновляем главный алиас 'vless' для менеджера
cat > "$BIN_DIR/vless" << 'VLESSEOF'
#!/bin/bash
exec bash /usr/local/bin/install.sh "$@"
VLESSEOF
chmod +x "$BIN_DIR/vless"
ln -sf "$BIN_DIR/vless" /usr/bin/vless 2>/dev/null || true

legacy_check
main_menu
