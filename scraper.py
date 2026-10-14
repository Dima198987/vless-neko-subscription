import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False

URL = "https://outlinekeys.com/protocols/vless/"
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

def main():
    # Создаем файл сразу, чтобы git не падал
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("# VLESS конфигурации с outlinekeys.com\n")

    print(f"Загрузка страницы {URL}...")
    print(f"Используется эмуляция браузера: {'curl_cffi (Chrome)' if USE_CURL_CFFI else 'стандартный requests'}")
    
    try:
        if USE_CURL_CFFI:
            response = requests.get(URL, impersonate="chrome120", timeout=15)
        else:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            }
            response = requests.get(URL, headers=headers, timeout=15)
            
        print(f"Статус ответа: {response.status_code}")
        
        # Проверка на блокировку Cloudflare
        if response.status_code == 403 or "cloudflare" in response.text.lower() or "checking your browser" in response.text.lower():
            print("ОШИБКА: Сайт заблокировал запрос (Cloudflare или защита от ботов).")
            print("Серверы GitHub Actions часто находятся в черных списках таких сайтов.")
            with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
                f.write("# ОШИБКА: Сайт заблокировал автоматический доступ\n")
            return

        response.raise_for_status()
    except Exception as e:
        print(f"Критическая ошибка при загрузке: {e}")
        return

    candidates = re.findall(r'vless://[^\s<>"\']+', response.text)
    
    configs = []
    for c in candidates:
        c = c.rstrip('.,);]')
        if c not in configs:
            configs.append(c)
            
    print(f"Найдено {len(configs)} уникальных конфигураций VLESS.")

    if not configs:
        print("Конфигурации не найдены. Возможно, сайт изменил верстку или отдает данные через JavaScript.")
        return

    print("Проверка доступности серверов (TCP)...")
    working_configs = []
    
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_to_config = {executor.submit(check_vless_working, config): config for config in configs}
        for future in as_completed(future_to_config):
            config = future_to_config[future]
            try:
                if future.result():
                    working_configs.append(config)
            except Exception:
                pass

    print(f"\nУспешно проверено: {len(working_configs)} из {len(configs)}")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        if working_configs:
            for config in working_configs:
                f.write(config + '\n')
        else:
            f.write("# Рабочих конфигураций не найдено\n")
            
    print(f"Результаты сохранены в {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
