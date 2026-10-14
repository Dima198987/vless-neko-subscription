import re
import socket
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

URL = "https://outlinekeys.com/"
OUTPUT_FILE = "vless_configs.txt"
TIMEOUT = 3  # Таймаут на проверку одного сервера в секундах

def check_vless_working(config):
    """Базовая проверка доступности хоста и порта по TCP."""
    try:
        clean_config = config.replace('vless://', '')
        parts = clean_config.split('@')
        if len(parts) != 2:
            return False
        
        host_info = parts[1]
        # Извлекаем host:port (всё до первого /, ? или #)
        host_port = re.split(r'[/?#]', host_info)[0]
        if ':' not in host_port:
            return False
            
        # Поддержка IPv6 [host]:port или обычного host:port
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
        
        # Попытка установить TCP-соединение
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT)
            s.connect((host, port))
            return True
    except Exception:
        return False

def main():
    print(f"Загрузка страницы {URL}...")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(URL, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Ошибка при загрузке страницы: {e}")
        return

    # Поиск всех строк, начинающихся с vless:// и не содержащих пробелов или HTML-символов
    candidates = re.findall(r'vless://[^\s<>"\']+', response.text)
    
    # Очистка от возможных конечных знаков препинания и удаление дубликатов с сохранением порядка
    configs = []
    for c in candidates:
        c = c.rstrip('.,);]')
        if c not in configs:
            configs.append(c)
            
    print(f"Найдено {len(configs)} уникальных конфигураций VLESS.")

    if not configs:
        print("Конфигурации не найдены. Возможно, сайт изменил структуру или заблокировал запрос.")
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# Конфигурации не найдены или сайт недоступен\n")
        return

    print("Проверка доступности серверов (TCP)...")
    working_configs = []
    
    # Параллельная проверка для ускорения процесса
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_to_config = {executor.submit(check_vless_working, config): config for config in configs}
        for future in as_completed(future_to_config):
            config = future_to_config[future]
            try:
                if future.result():
                    working_configs.append(config)
                    print(f"[OK] {config[:50]}...")
                else:
                    print(f"[FAIL] {config[:50]}...")
            except Exception as e:
                print(f"[ERROR] {config[:50]}... : {e}")

    print(f"\nУспешно проверено: {len(working_configs)} из {len(configs)}")

    # Сохранение рабочих конфигураций в файл
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for config in working_configs:
            f.write(config + '\n')
            
    print(f"Результаты сохранены в {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
