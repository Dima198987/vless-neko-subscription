import re
import socket
import base64
import requests
from urllib.parse import urlparse, parse_qsl, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# ИСТОЧНИКИ
SOURCE_URLS = [
    "https://r.jina.ai/https://t.me/s/v2ray_configs_pool",
    "https://r.jina.ai/https://t.me/s/ConfigV2rayNG",
    "https://r.jina.ai/https://t.me/s/vless_vpn",
    "https://r.jina.ai/https://t.me/s/OutlineVpnOfficial",
    "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/list/00.txt",
    "https://raw.githubusercontent.com/Leon406/SubCrawler/main/sub/share/all",
]

OUTPUT_FILE = "vless_configs.txt"
TIMEOUT = 3

def get_canonical_key(config):
    """
    Превращает конфигурацию в канонический (эталонный) вид для идеального поиска дубликатов.
    1. Убирает пробелы по краям.
    2. Парсит URL.
    3. Сортирует параметры запроса (чтобы ?a=1&b=2 и ?b=2&a=1 стали одинаковыми).
    4. Удаляет фрагмент (название после #).
    5. Приводит к нижнему регистру.
    """
    try:
        config = config.strip()
        if not config.startswith('vless://'):
            return config
            
        parsed = urlparse(config)
        
        # Сортируем параметры: parse_qsl разбивает на пары, sorted сортирует по ключу
        sorted_query = '&'.join(f"{k}={v}" for k, v in sorted(parse_qsl(parsed.query)))
        
        # Собираем URL обратно БЕЗ фрагмента (последний аргумент - пустая строка)
        canonical = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            sorted_query,
            '' 
        ))
        return canonical.lower()
    except Exception:
        # Если парсинг не удался, используем запасной простой метод
        return config.split('#')[0].strip().lower()

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
    clean = re.sub(r'\s+', '', text)
    try:
        decoded = base64.b64decode(clean, validate=False)
        return decoded.decode('utf-8', errors='ignore')
    except Exception:
        return text

def fetch_and_extract(url):
    configs = []
    try:
        print(f"[INFO] Загрузка: {url}")
        response = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        if response.status_code != 200:
            print(f"[WARN] Источник вернул код {response.status_code}")
            return configs
            
        text = response.text
        if "jina.ai" not in url:
            text = try_decode_base64(text)
            
        found = re.findall(r'vless://[^\s<>"\']+', text)
        for c in found:
            c = c.rstrip('.,);]')
            if c:
                configs.append(c)
                
        print(f"[INFO] Сырых ссылок найдено: {len(configs)}")
    except Exception as e:
        print(f"[ERROR] Ошибка {url}: {type(e).__name__}: {e}")
    return configs

def main():
    print("=" * 60)
    print("СБОР И ГЛУБОКАЯ ОЧИСТКА КОНФИГУРАЦИЙ VLESS")
    print("=" * 60)
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("# VLESS конфигурации\n")

    all_configs = []
    for url in SOURCE_URLS:
        all_configs.extend(fetch_and_extract(url))
        
    print("\n" + "=" * 60)
    print("ГЛУБОКАЯ ДЕДУПЛИКАЦИЯ (нормализация URL + сортировка параметров)...")
    
    seen_keys = set()
    final_configs = []
    duplicates_removed = 0
    
    for c in all_configs:
        key = get_canonical_key(c)
        if key not in seen_keys:
            seen_keys.add(key)
            final_configs.append(c) # Сохраняем оригинальную строку с красивым названием
        else:
            duplicates_removed += 1
            
    print(f"Всего собрано сырых ссылок: {len(all_configs)}")
    print(f"Удалено точных и скрытых дубликатов: {duplicates_removed}")
    print(f"Осталось уникальных профилей: {len(final_configs)}")
    print("=" * 60)

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
