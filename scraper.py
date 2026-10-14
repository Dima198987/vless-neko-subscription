import re
import socket
import base64
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Расширенный список проверенных источников.
# Если какой-то источник перестанет работать — просто закомментируйте его.
SOURCE_URLS = [
    # Агрегатор из Telegram-каналов (Base64)
    "https://raw.githubusercontent.com/yebekhe/TelegramV2rayCollector/main/sub/mix/base64",
    # Агрегатор VLESS из Telegram (Base64)
    "https://raw.githubusercontent.com/soroushmirzaei/telegram-configs-collector/main/protocols/vless",
    # Общий агрегатор (Base64)
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/All_Configs_base64.txt",
    # Агрегатор (Base64)
    "https://raw.githubusercontent.com/sashalsk/V2Ray/main/V2Config_64base",
    # Текстовый список
    "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/list/00.txt",
    # Еще один агрегатор (Base64)
    "https://raw.githubusercontent.com/MrPooyaX/VpnsFucking/main/Shenzo-IP.txt",
]

OUTPUT_FILE = "vless_configs.txt"
TIMEOUT = 3

def check_vless_working(config):
    """Базовая проверка доступности хоста и порта по TCP."""
    try:
        clean_config = config.replace('vless://', '')
        parts = clean_config.split('@')
        if len(parts) != 2:
            return False
        
        host_info = parts[1]
        host_port = re.split(r'[/?#]', host_info)[0]
        if ':' not in host_port:
            return False
            
        if host_port.startswith('['):
            match = re.match(r'\[([^\]]+)\]:(\d+)', host_port)
            if not match:
                return False
            host, port_str = match.groups()
            family = socket.AF_INET6
        else:
            host, port_str = host_port.rsplit(':', 1)
            family = socket.AF_INET
            
        port = int(port_str)
        
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT)
            s.connect((host, port))
            return True
    except Exception:
        return False

def try_decode_base64(text):
    """Пытается декодировать текст из Base64 несколькими способами."""
    # Способ 1: Очистка от всех пробелов и переносов
    clean = re.sub(r'\s+', '', text)
    try:
        decoded = base64.b64decode(clean, validate=False)
        # Проверяем, что результат читаемый (много печатных символов)
        decoded_text = decoded.decode('utf-8', errors='ignore')
        if 'vless://' in decoded_text or 'vmess://' in decoded_text or '://' in decoded_text:
            return decoded_text
    except Exception:
        pass
    
    # Способ 2: Декодирование построчно
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            decoded = base64.b64decode(line, validate=False).decode('utf-8', errors='ignore')
            lines.append(decoded)
        except Exception:
            lines.append(line)
    return '\n'.join(lines)

def fetch_and_extract(url):
    """Скачивает файл, декодирует и извлекает vless:// ссылки."""
    configs = []
    raw_text = ""
    try:
        print(f"[INFO] Загрузка: {url}")
        response = requests.get(url, timeout=20, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        print(f"[INFO] Статус ответа: {response.status_code}, Размер: {len(response.text)} байт")
        
        if response.status_code != 200:
            print(f"[WARN] Источник вернул код {response.status_code}")
            return configs
            
        raw_text = response.text
        
        # Если в исходном тексте уже есть vless:// — работаем с ним напрямую
        if 'vless://' in raw_text:
            print("[INFO] Найден прямой текст с vless:// ссылками")
            text = raw_text
        else:
            # Пытаемся декодировать Base64
            print("[INFO] Попытка декодирования Base64...")
            text = try_decode_base64(raw_text)
            if 'vless://' in text:
                print("[INFO] Base64 успешно декодирован, найдены vless:// ссылки")
            else:
                print("[WARN] После декодирования vless:// ссылки не найдены")
        
        # Извлекаем все vless:// ссылки
        found = re.findall(r'vless://[^\s<>"\']+', text)
        for c in found:
            c = c.rstrip('.,);]')
            if c not in configs:
                configs.append(c)
                
        print(f"[INFO] Итог по источнику: найдено {len(configs)} уникальных vless:// ссылок")
    except Exception as e:
        print(f"[ERROR] Ошибка при обработке {url}: {type(e).__name__}: {e}")
        
    return configs

def main():
    print("=" * 60)
    print("НАЧАЛО СБОРА КОНФИГУРАЦИЙ VLESS")
    print("=" * 60)
    
    all_configs = []
    source_stats = []
    
    for url in SOURCE_URLS:
        configs = fetch_and_extract(url)
        source_stats.append((url, len(configs)))
        all_configs.extend(configs)
        
    # Глобальная дедупликация
    final_configs = []
    for c in all_configs:
        if c not in final_configs:
            final_configs.append(c)
            
    print("\n" + "=" * 60)
    print("СТАТИСТИКА ПО ИСТОЧНИКАМ:")
    for url, count in source_stats:
        print(f"  {count:4d} ссылок <- {url}")
    print(f"ВСЕГО УНИКАЛЬНЫХ: {len(final_configs)}")
    print("=" * 60)

    # Если ничего не нашли — сохраняем диагностический файл
    if not final_configs:
        print("\n[CRITICAL] Ни один источник не вернул vless:// ссылок!")
        print("Возможные причины:")
        print("  - Источники изменили формат или стали недоступны")
        print("  - GitHub Actions блокирует исходящие запросы")
        print("  - Все источники используют формат, отличный от vless://")
        
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# ОШИБКА СБОРА\n")
            f.write("# Не удалось получить ни одной vless:// конфигурации.\n")
            f.write("# Проверьте логи GitHub Actions для диагностики.\n")
            f.write(f"# Источников в списке: {len(SOURCE_URLS)}\n")
        return

    # Проверка доступности (TCP)
    print("\nПроверка доступности серверов (TCP)...")
    working_configs = []
    
    with ThreadPoolExecutor(max_workers=40) as executor:
        future_to_config = {executor.submit(check_vless_working, config): config for config in final_configs}
        for future in as_completed(future_to_config):
            config = future_to_config[future]
            try:
                if future.result():
                    working_configs.append(config)
            except Exception:
                pass

    print(f"Успешно проверено: {len(working_configs)} из {len(final_configs)}")

    # Сохранение результата
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        if working_configs:
            for config in working_configs:
                f.write(config + '\n')
            print(f"Сохранено {len(working_configs)} РАБОЧИХ конфигураций.")
        else:
            # Если TCP-проверка ничего не прошла (частая проблема GitHub Actions) —
            # сохраняем ВСЕ найденные конфиги, чтобы файл не был пустым
            print("[WARN] TCP-проверка не нашла рабочих серверов.")
            print("Это часто случается из-за сетевых ограничений GitHub Actions.")
            print("Сохраняем ВСЕ найденные конфигурации без проверки.")
            for config in final_configs:
                f.write(config + '\n')
            
    print(f"\nФайл {OUTPUT_FILE} успешно сохранен.")

if __name__ == "__main__":
    main()
