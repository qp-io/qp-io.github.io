# 🛠️ QP-IO Config Tools  
**https://qp-io.github.io/
Генератор конфигов для Singbox и редактор MagiTrickle для маршрутизаторов Keenetic**

---

## 🌟 Основные возможности
- **Singbox Tun Config Generator** – генерация конфигурационных файлов для Singbox
- **MagiTrickle Editor** – настройка туннелирования трафика через MagiTrickle
- Поддержка обновлений Singbox для архитектур `mipsel` и `aarch64`
- Актуальная версия: **1.12.0-beta.14**

---

## 🚀 Быстрое обновление Singbox на Keenetic

### Для процессоров `mipsel`
```bash
curl -L -o /tmp/sing-box.zip https://github.com/qp-io/qp-io.github.io/raw/refs/heads/main/sing-box_mipsel.zip  \
&& unzip -o /tmp/sing-box.zip -d /tmp \
&& /opt/etc/init.d/S99sing-box stop \
&& rm -rf /opt/bin/sing-box \
&& cp /tmp/sing-box /opt/bin/sing-box \
&& chmod +x /opt/bin/sing-box \
&& /opt/etc/init.d/S99sing-box start
```
### Для процессоров `aarch64`
```bash
curl -L -o /tmp/sing-box.zip https://github.com/qp-io/qp-io.github.io/raw/refs/heads/main/sing-box_aarch64.zip  \
&& unzip -o /tmp/sing-box.zip -d /tmp \
&& /opt/etc/init.d/S99sing-box stop \
&& rm -rf /opt/bin/sing-box \
&& cp /tmp/sing-box /opt/bin/sing-box \
&& chmod +x /opt/bin/sing-box \
&& /opt/etc/init.d/S99sing-box start
```
