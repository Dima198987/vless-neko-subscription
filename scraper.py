import re
import socket
import base64
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ИСТОЧНИКИ:
# 1. Прямые ссылки на GitHub (если они живы)
# 2. Telegram-каналы через r.jina.ai (читает веб-версию канала и отдает чистый текст со всеми ссылками)
SOURCE_URLS = [
    # Telegram каналы (через Jina AI Reader - самый надежный способ)
    "https://r.jina.ai/https://t.me/s/v2ray_configs_pool",
    "https://r.jina.ai/https://t.me/s/ConfigV2rayNG",
    "https://r.jina.ai/https://t.me/s/vless_vpn",
    
    # GitHub репозитории (актуальные на текущий момент)
    "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/list/00.txt",
    "https://raw.githubusercontent.com/Leon406/SubCrawler/main/sub/share/all",
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Config.txt",
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
    """Пытается декодировать текст из Base64."""
    clean = re.sub(r'\s+', '', text)
    try:
        decoded = base64.b64decode(clean, validate=False)
        return decoded.decode('utf-8', errors='ignore')
    except Exception:
        return text

def fetch_and_extract(url):
    """Скачивает файл, извлекает vless:// ссылки."""
    configs = []
    try:
        print(f"[INFO] Загрузка: {url}")
        # Увеличиваем таймаут для Jina AI, так как парсинг Telegram может занять 2-3 секунды
        response = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        print(f"[INFO] Статус: {response.status_code}, Размер: {len(response.text)} байт")
        
        if response.status_code != 200:
            print(f"[WARN] Источник вернул код {response.status_code}")
            return configs
            
        text = response.text
        
        # Если это не Jina AI, пробуем декодировать Base64
        if "jina.ai" not in url:
            text = try_decode_base64(text)
            
        # Ищем vless://
        found = re.findall(r'vless://[^\s<>"\']+', text)
        
        # ДИАГНОСТИКА: Если не нашли vless, покажем, что там есть на самом деле
        if not found and len(text) > 50:
            other_protocols = re.findall(r'(vmess|trojan|ss|tuic|hysteria)://[^\s<>"\']+', text)
            print(f"[WARN] vless:// не найден. Найдены другие протоколы: {set(other_protocols)}")
            print(f"[DEBUG] Фрагмент текста: {text[:150].replace(chr(10), ' ')}...")
            
        for c in found:
            c = c.rstrip('.,);]')
            if c not in configs:
                configs.append(c)
                
        print(f"[INFO] Найдено уникальных vless:// ссылок: {len(configs)}")
    except Exception as e:
        print(f"[ERROR] Ошибка {url}: {type(e).__name__}: {e}")
        
    return configs

def main():
    print("=" * 60)
    print("НАЧАЛО СБОРА КОНФИГУРАЦИЙ VLESS")
    print("=" * 60)
    
    # Гарантируем создание файла
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("# VLESS конфигурации\n")

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
    print(f"ВСЕГО УНИКАЛЬНЫХ VLESS: {len(final_configs)}")
    print("=" * 60)

    if not final_configs:
        print("\n[CRITICAL] Не найдено ни одной ссылки vless://")
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# ОШИБКА: Источники не содержат vless:// конфигураций\n")
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

    print(f"Успешно прошло TCP-проверку: {len(working_configs)} из {len(final_configs)}")

    # Сохранение результата
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        if working_configs:
            for config in working_configs:
                f.write(config + '\n')
            print(f"Сохранено {len(working_configs)} РАБОЧИХ конфигураций.")
        else:
            print("[WARN] TCP-проверка не прошла ни один сервер (ограничения сети GitHub).")
            print("Сохраняем ВСЕ найденные конфигурации без проверки.")
            for config in final_configs:
                f.write(config + '\n')
            
    print(f"\nФайл {OUTPUT_FILE} успешно обновлен.")

if __name__ == "__main__":
    main()
