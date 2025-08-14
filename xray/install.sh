#!/bin/bash

# Reality-EZPZ Installer Script with Menu
# Автор: Основан на оригинальном скрипте от qp-io

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Функция для вывода цветного текста
print_color() {
    local color=$1
    local text=$2
    echo -e "${color}${text}${NC}"
}

# Функция для вывода заголовка
print_header() {
    clear
    echo "=================================================="
    print_color $CYAN "           Reality-EZPZ Installer"
    echo "=================================================="
    echo ""
}

# Функция для валидации Telegram Bot Token
validate_telegram_token() {
    local token=$1
    local regex="^[0-9]{8,10}:[a-zA-Z0-9_-]{35}$"
    
    if [[ ! $token =~ $regex ]]; then
        print_color $RED "❌ Неверный формат токена Telegram бота!"
        return 1
    fi
    
    # Проверка токена через API Telegram
    if ! curl -sSfL -m 3 "https://api.telegram.org/bot${token}/getMe" >/dev/null 2>&1; then
        print_color $RED "❌ Токен Telegram бота неверный или бот недоступен!"
        return 1
    fi
    
    return 0
}

# Функция для валидации имен админов Telegram
validate_telegram_admins() {
    local admins=$1
    local regex="^[a-zA-Z][a-zA-Z0-9_]{4,31}(,[a-zA-Z][a-zA-Z0-9_]{4,31})*$"
    
    if [[ ! $admins =~ $regex ]]; then
        print_color $RED "❌ Неверный формат имен админов!"
        print_color $YELLOW "Формат: username1,username2 (без @ и пробелов)"
        return 1
    fi
    
    if [[ $admins =~ .+_$ ]] || [[ $admins =~ .+_,.+ ]]; then
        print_color $RED "❌ Имена админов не должны заканчиваться на '_' или содержать '_,' "
        return 1
    fi
    
    return 0
}

# Функция выбора core (xray или sing-box)
select_core() {
    print_header
    print_color $BLUE "🔧 Выберите движок (core):"
    echo ""
    echo "1) Xray - Стабильный и проверенный"
    echo "2) Sing-box - Современный с дополнительными возможностями"
    echo ""
    
    while true; do
        read -p "Введите ваш выбор (1-2): " core_choice
        case $core_choice in
            1)
                CORE="xray"
                print_color $GREEN "✅ Выбран Xray"
                break
                ;;
            2)
                CORE="sing-box"
                print_color $GREEN "✅ Выбран Sing-box"
                break
                ;;
            *)
                print_color $RED "❌ Неверный выбор. Введите 1 или 2."
                ;;
        esac
    done
    
    sleep 1
}

# Функция выбора использования Telegram бота
select_telegram_bot() {
    print_header
    print_color $BLUE "🤖 Использовать Telegram бот для управления?"
    echo ""
    echo "1) Да - включить Telegram бот"
    echo "2) Нет - установить без бота"
    echo ""
    
    while true; do
        read -p "Введите ваш выбор (1-2): " bot_choice
        case $bot_choice in
            1)
                USE_TELEGRAM_BOT=true
                print_color $GREEN "✅ Telegram бот будет включен"
                break
                ;;
            2)
                USE_TELEGRAM_BOT=false
                print_color $GREEN "✅ Установка без Telegram бота"
                break
                ;;
            *)
                print_color $RED "❌ Неверный выбор. Введите 1 или 2."
                ;;
        esac
    done
    
    sleep 1
}

# Функция для настройки Telegram бота
configure_telegram_bot() {
    if [ "$USE_TELEGRAM_BOT" = true ]; then
        print_header
        print_color $BLUE "🔑 Настройка Telegram бота"
        echo ""
        
        # Запрос токена бота
        while true; do
            echo "Для получения токена бота:"
            print_color $CYAN "1. Откройте @BotFather в Telegram"
            print_color $CYAN "2. Отправьте /newbot и следуйте инструкциям"
            print_color $CYAN "3. Скопируйте полученный токен"
            echo ""
            read -p "Введите токен Telegram бота: " TELEGRAM_TOKEN
            
            if validate_telegram_token "$TELEGRAM_TOKEN"; then
                print_color $GREEN "✅ Токен проверен и валиден!"
                break
            else
                print_color $YELLOW "Попробуйте еще раз..."
                echo ""
            fi
        done
        
        echo ""
        
        # Запрос админов
        while true; do
            print_color $CYAN "Введите логины админов Telegram (без @):"
            print_color $YELLOW "Формат: admin1,admin2,admin3 (разделенные запятой, без пробелов)"
            echo ""
            read -p "Логины админов: " TELEGRAM_ADMINS
            
            if validate_telegram_admins "$TELEGRAM_ADMINS"; then
                print_color $GREEN "✅ Логины админов корректны!"
                break
            else
                print_color $YELLOW "Попробуйте еще раз..."
                echo ""
            fi
        done
    fi
}

# Функция отображения конфигурации
show_configuration() {
    print_header
    print_color $BLUE "📋 Конфигурация установки:"
    echo ""
    print_color $CYAN "Core: $CORE"
    print_color $CYAN "Telegram бот: $([ "$USE_TELEGRAM_BOT" = true ] && echo "Включен" || echo "Отключен")"
    
    if [ "$USE_TELEGRAM_BOT" = true ]; then
        print_color $CYAN "Токен бота: ${TELEGRAM_TOKEN:0:10}***"
        print_color $CYAN "Админы: $TELEGRAM_ADMINS"
    fi
    
    echo ""
    print_color $YELLOW "Продолжить установку с этими параметрами?"
    echo ""
    echo "1) Да - продолжить установку"
    echo "2) Нет - изменить конфигурацию"
    echo "3) Выход"
    echo ""
    
    while true; do
        read -p "Введите ваш выбор (1-3): " confirm_choice
        case $confirm_choice in
            1)
                return 0
                ;;
            2)
                return 1
                ;;
            3)
                print_color $YELLOW "Установка отменена."
                exit 0
                ;;
            *)
                print_color $RED "❌ Неверный выбор. Введите 1, 2 или 3."
                ;;
        esac
    done
}

# Функция загрузки и установки оригинального скрипта
install_reality_ezpz() {
    print_header
    print_color $BLUE "🚀 Загрузка и установка скрипта..."
    echo ""
    
    # Загружаем оригинальный скрипт в /usr/local/bin
    print_color $CYAN "Загрузка оригинального скрипта..."
    if ! curl -fsSL -o "/usr/local/bin/reality-ezpz.sh" "https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh"; then
        print_color $RED "❌ Ошибка загрузки скрипта!"
        exit 1
    fi
    
    # Делаем скрипт исполняемым
    chmod +x "/usr/local/bin/reality-ezpz.sh"
    
    print_color $GREEN "✅ Скрипт загружен в /usr/local/bin/"
    echo ""
    
    # Создаем алиас vless
    setup_vless_alias
    
    # Тестируем команду
    test_vless_command
    
    # Строим команду запуска
    INSTALL_COMMAND="/usr/local/bin/reality-ezpz.sh --core=$CORE"
    
    if [ "$USE_TELEGRAM_BOT" = true ]; then
        INSTALL_COMMAND="$INSTALL_COMMAND --enable-tgbot=true --tgbot-token=$TELEGRAM_TOKEN --tgbot-admins=$TELEGRAM_ADMINS"
    else
        INSTALL_COMMAND="$INSTALL_COMMAND --enable-tgbot=false"
    fi
    
    print_color $BLUE "🔧 Запуск установки с параметрами:"
    print_color $CYAN "Команда: $(echo $INSTALL_COMMAND | sed "s/$TELEGRAM_TOKEN/***TOKEN***/g")"
    echo ""
    
    # Запускаем установку
    print_color $GREEN "🚀 Начинаем установку..."
    sleep 2
    
    eval "$INSTALL_COMMAND"
}

# Функция создания алиаса vless
setup_vless_alias() {
    print_color $CYAN "🔗 Создание алиаса 'vless'..."
    
    # Создаем wrapper скрипт
    cat > "/usr/local/bin/vless" << 'EOF'
#!/bin/bash

REALITY_SCRIPT="/usr/local/bin/reality-ezpz.sh"

case "${1:-}" in
    -m|--menu)
        exec "$REALITY_SCRIPT" --menu
        ;;
    -r|--restart)
        exec "$REALITY_SCRIPT" --restart
        ;;
    -u|--uninstall)
        echo "🗑️  Удаление Reality-EZPZ..."
        exec "$REALITY_SCRIPT" --uninstall
        ;;
    -h|--help|help)
        echo "=================================================="
        echo "           VLESS Management Tool"
        echo "=================================================="
        echo ""
        echo "Использование: vless [ОПЦИЯ]"
        echo ""
        echo "Опции:"
        echo "  -m, --menu        Открыть меню управления"
        echo "  -r, --restart     Перезапустить сервисы"
        echo "  -u, --uninstall   Удалить Reality-EZPZ"
        echo "  -h, --help        Показать эту справку"
        echo ""
        echo "Дополнительные команды:"
        echo "  vless config      Показать конфигурацию сервера"
        echo "  vless users       Показать список пользователей"
        echo "  vless add <user>  Добавить пользователя"
        echo "  vless del <user>  Удалить пользователя"
        echo "  vless show <user> Показать конфигурацию пользователя"
        echo "  vless backup      Создать резервную копию"
        echo ""
        echo "Примеры:"
        echo "  vless -m                    # Открыть меню"
        echo "  vless add testuser          # Добавить пользователя"
        echo "  vless show testuser         # Показать QR код"
        echo ""
        ;;
    config|show-config)
        exec "$REALITY_SCRIPT" --show-server-config
        ;;
    users|list-users)
        exec "$REALITY_SCRIPT" --list-users
        ;;
    add)
        if [ -z "$2" ]; then
            echo "❌ Укажите имя пользователя: vless add <username>"
            exit 1
        fi
        exec "$REALITY_SCRIPT" --add-user "$2"
        ;;
    del|delete)
        if [ -z "$2" ]; then
            echo "❌ Укажите имя пользователя: vless del <username>"
            exit 1
        fi
        exec "$REALITY_SCRIPT" --delete-user "$2"
        ;;
    show)
        if [ -z "$2" ]; then
            echo "❌ Укажите имя пользователя: vless show <username>"
            exit 1
        fi
        exec "$REALITY_SCRIPT" --show-user "$2"
        ;;
    backup)
        exec "$REALITY_SCRIPT" --backup
        ;;
    restore)
        if [ -z "$2" ]; then
            echo "❌ Укажите файл или URL: vless restore <file_or_url>"
            exit 1
        fi
        exec "$REALITY_SCRIPT" --restore "$2"
        ;;
    *)
        if [ -n "$1" ]; then
            # Передаем все неизвестные параметры оригинальному скрипту
            exec "$REALITY_SCRIPT" "$@"
        else
            # Показываем краткую справку
            echo "=================================================="
            echo "           VLESS Management Tool"
            echo "=================================================="
            echo ""
            echo "Быстрые команды:"
            echo "  vless -m    Меню управления"
            echo "  vless -r    Перезапуск сервисов"
            echo "  vless -u    Удаление"
            echo "  vless -h    Полная справка"
            echo ""
        fi
        ;;
esac
EOF

    chmod +x "/usr/local/bin/vless"
    
    # Добавляем /usr/local/bin в PATH если его там нет
    add_to_path
    
    # Создаем символическую ссылку в /usr/bin для надежности
    ln -sf "/usr/local/bin/vless" "/usr/bin/vless" 2>/dev/null || true
    
    # Обновляем PATH в текущей сессии
    export PATH="/usr/local/bin:$PATH"
    
    # Проверяем доступность команды
    if command -v vless >/dev/null 2>&1; then
        print_color $GREEN "✅ Команда 'vless' доступна!"
    else
        print_color $YELLOW "⚠️  Команда 'vless' будет доступна после перезахода в систему"
    fi
    
    echo ""
    print_color $YELLOW "Доступные команды:"
    print_color $CYAN "  vless -m  (меню управления)"
    print_color $CYAN "  vless -r  (перезапуск)"
    print_color $CYAN "  vless -u  (удаление)"
    echo ""
}

# Функция добавления /usr/local/bin в PATH
add_to_path() {
    # Проверяем, есть ли /usr/local/bin в PATH
    if [[ ":$PATH:" != *":/usr/local/bin:"* ]]; then
        print_color $CYAN "Добавление /usr/local/bin в PATH..."
        
        # Добавляем в различные конфигурационные файлы
        local profile_files=(
            "/etc/profile"
            "/root/.bashrc"
            "/root/.profile"
        )
        
        local path_line='export PATH="/usr/local/bin:$PATH"'
        
        for file in "${profile_files[@]}"; do
            if [ -w "$file" ] && ! grep -q "/usr/local/bin" "$file" 2>/dev/null; then
                echo "" >> "$file"
                echo "# Added by Reality-EZPZ installer" >> "$file"
                echo "$path_line" >> "$file"
            fi
        done
        
        # Добавляем в /etc/environment если существует
        if [ -w "/etc/environment" ]; then
            if grep -q "^PATH=" /etc/environment; then
                sed -i 's|^PATH="|PATH="/usr/local/bin:|' /etc/environment
            else
                echo 'PATH="/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' >> /etc/environment
            fi
        fi
    fi
}

# Функция тестирования команды vless
test_vless_command() {
    print_color $BLUE "🧪 Тестирование команды 'vless'..."
    
    # Обновляем PATH
    export PATH="/usr/local/bin:/usr/bin:$PATH"
    hash -r 2>/dev/null || true
    
    # Тестируем команду
    if /usr/local/bin/vless -h >/dev/null 2>&1; then
        print_color $GREEN "✅ Команда 'vless' работает корректно!"
        
        # Показываем пример использования
        echo ""
        print_color $CYAN "Попробуйте прямо сейчас:"
        print_color $YELLOW "  /usr/local/bin/vless -m"
        echo ""
        print_color $CYAN "Или после перезахода просто:"
        print_color $YELLOW "  vless -m"
        
    else
        print_color $RED "❌ Ошибка при тестировании команды"
    fi
    
    echo ""
}

# Главная функция
main() {
    # Проверка прав root
    if [[ $EUID -ne 0 ]]; then
        print_color $RED "❌ Этот скрипт должен быть запущен с правами root!"
        print_color $YELLOW "Используйте: sudo $0"
        exit 1
    fi
    
    # Инициализация переменных
    CORE=""
    USE_TELEGRAM_BOT=false
    TELEGRAM_TOKEN=""
    TELEGRAM_ADMINS=""
    
    # Основной цикл конфигурации
    while true; do
        # Выбор core
        select_core
        
        # Выбор использования Telegram бота
        select_telegram_bot
        
        # Настройка Telegram бота если выбран
        configure_telegram_bot
        
        # Показать конфигурацию и запросить подтверждение
        if show_configuration; then
            break
        fi
        
        # Сброс переменных для повторной настройки
        CORE=""
        USE_TELEGRAM_BOT=false
        TELEGRAM_TOKEN=""
        TELEGRAM_ADMINS=""
    done
    
    # Запуск установки
    install_reality_ezpz
    
    # Финальное сообщение
    print_header
    print_color $GREEN "🎉 Установка завершена!"
    echo ""
    print_color $BLUE "📋 Управление сервером:"
    print_color $GREEN "  /usr/local/bin/vless -m    # Открыть меню (работает сейчас)"
    print_color $GREEN "  /usr/local/bin/vless -r    # Перезапустить сервисы"
    print_color $GREEN "  /usr/local/bin/vless -u    # Удалить Reality-EZPZ"
    echo ""
    print_color $YELLOW "После перезахода в систему доступно короткое имя:"
    print_color $CYAN "  vless -m    # Меню"
    print_color $CYAN "  vless -r    # Рестарт"
    print_color $CYAN "  vless -u    # Удаление"
    echo ""
    print_color $BLUE "🔧 Дополнительные команды:"
    print_color $CYAN "  /usr/local/bin/vless config      # Показать конфигурацию"
    print_color $CYAN "  /usr/local/bin/vless users       # Список пользователей"
    print_color $CYAN "  /usr/local/bin/vless add <user>  # Добавить пользователя"
    print_color $CYAN "  /usr/local/bin/vless show <user> # Показать QR код"
    echo ""
    if [ "$USE_TELEGRAM_BOT" = true ]; then
        print_color $PURPLE "📱 Telegram бот активен и готов к работе"
        print_color $YELLOW "Отправьте /start боту для начала управления"
        echo ""
    fi
    
    # Предлагаем запустить меню прямо сейчас
    print_color $GREEN "🚀 Хотите открыть меню управления прямо сейчас? (y/n)"
    read -p "Ответ: " open_menu
    if [[ "$open_menu" =~ ^[Yy]$ ]]; then
        echo ""
        print_color $BLUE "Запуск меню..."
        sleep 1
        exec /usr/local/bin/vless -m
    fi
    
    echo ""
}

# Запуск главной функции
main "$@"