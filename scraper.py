import re
import socket
import base64
import requests
from urllib.parse import urlparse, parse_qsl, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# ИСТОЧНИКИ: Telegram-каналы через Jina AI (обходит любые блокировки) + надежные GitHub-агрегаторы
SOURCE_URLS = [
    # 1. Самый крупный и активный канал с конфигами для v2rayNG (много Reality)
    "https://r.jina.ai/https://t.me/s/v2ray_configs_pool",
    
    # 2. Канал, ориентированный именно на пользователей из РФ (тестируют обход DPI)
    "https://r.jina.ai/https://t.me/s/V2rayNGmat",
    
    # 3. Очень активный канал с большим объемом VLESS и Trojan конфигов
    "https://r.jina.ai/https://t.me/s/ShadowProxy66",
    
    # 4. Официальный канал того самого сайта outlinekeys.com (дублирует их ключи в текстовом виде)
    "https://r.jina.ai/https://t.me/s/OutlineVpnOfficial",
    
    # 5. Резервный GitHub-агрегатор, который собирает данные из Telegram и выдает чистый список
    "https://raw.githubusercontent.com/Mahdi0024/ProxyCollector/master/sub/proxies.txt",
]

OUTPUT_FILE = "vless_configs.txt"
TIMEOUT = 3

def clean_config_string(config):
    config = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', config)
    config = config.strip()
    config = re.sub(r'\s+', ' ', config)
    return config

def get_canonical_key(config):
    """Канонический вид для поиска дубликатов (игнорирует название после #)."""
    try:
        config = clean_config_string(config)
        if not config.startswith('vless://'):
            return config
        parsed = urlparse(config)
        sorted_query = '&'.join(f"{k}={v}" for k, v in sorted(parse_qsl(parsed.query)))
        canonical = urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, sorted_query, ''
        ))
        return canonical.lower()
    except Exception:
        return clean_config_string(config).split('#')[0].lower()

def check_vless_working(config):
    """Простая и быстрая TCP-проверка доступности хоста и порта."""
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

def fetch_and_extract(url):
    configs = []
    try:
        print(f"[INFO] Загрузка: {url}")
        # Увеличиваем таймаут до 30 сек, так как Jina AI иногда обрабатывает большие каналы пару секунд
        response = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        if response.status_code != 200:
            print(f"[WARN] Источник вернул код {response.status_code}")
            return configs
            
        text = response.text
        
        # Ищем все vless:// ссылки в чистом тексте от Jina AI
        found = re.findall(r'vless://[^\s<>"\']+', text)
        
        for c in found:
            c = c.rstrip('.,);]')
            c = clean_config_string(c)
            if c:
                configs.append(c)
                
        print(f"[INFO] Найдено vless:// ссылок: {len(configs)}")
    except Exception as e:
        print(f"[ERROR] Ошибка {url}: {type(e).__name__}: {e}")
    return configs

def main():
    print("=" * 70)
    print("СБОР VLESS КОНФИГУРАЦИЙ (TELEGRAM + JINA AI)")
    print("Обход блокировок РКН и Cloudflare")
    print("=" * 70)
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("# VLESS конфигурации\n")

    all_configs = []
    source_stats = []
    
    for url in SOURCE_URLS:
        configs = fetch_and_extract(url)
        source_stats.append((url, len(configs)))
        all_configs.extend(configs)
        
    print("\n" + "=" * 70)
    print("СТАТИСТИКА СБОРА:")
    for url, count in source_stats:
        # Сокращаем URL для красивого вывода в логах
        short_url = url.replace('https://r.jina.ai/https://t.me/s/', 'TG: ')
        print(f"  {count:4d} шт. <- {short_url}")
    print("=" * 70)

    print("\nГЛУБОКАЯ ДЕДУПЛИКАЦИЯ...")
    seen_keys = set()
    final_configs = []
    duplicates_removed = 0
    
    for c in all_configs:
        key = get_canonical_key(c)
        if key not in seen_keys:
            seen_keys.add(key)
            final_configs.append(c)
        else:
            duplicates_removed += 1
            
    print(f"Всего собрано: {len(all_configs)}")
    print(f"Удалено дубликатов: {duplicates_removed}")
    print(f"Осталось уникальных: {len(final_configs)}")
    print("=" * 70)

    if not final_configs:
        print("\n[CRITICAL] Не найдено ни одной ссылки vless://")
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# ОШИБКА: Источники не содержат vless:// конфигураций\n")
        return

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

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        if working_configs:
            for config in working_configs:
                f.write(config + '\n')
            print(f"✅ Сохранено {len(working_configs)} РАБОЧИХ уникальных конфигураций.")
        else:
            print("[WARN] TCP-проверка не прошла ни один сервер (ограничения сети GitHub).")
            print("💡 Сохраняем ВСЕ уникальные конфигурации без проверки.")
            for config in final_configs:
                f.write(config + '\n')
            
    print(f"\n🎉 Файл {OUTPUT_FILE} успешно обновлен!")

if __name__ == "__main__":
    main()
