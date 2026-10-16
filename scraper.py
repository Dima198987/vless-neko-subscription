import re
import socket
import base64
import requests
from urllib.parse import urlparse, parse_qsl, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# СПЕЦИАЛИЗИРОВАННЫЕ ИСТОЧНИКИ ДЛЯ РФ (VLESS + REALITY)
# Эти репозитории собирают конфигурации, которые обходят DPI мобильных операторов (включая Йоту)
SOURCE_URLS = [
    # 1. Специально для РФ: VLESS REALITY с белыми списками SNI для мобильных операторов
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    
    # 2. Крупный агрегатор, содержащий много REALITY конфигураций (обновляется ежедневно)
    "https://raw.githubusercontent.com/Mahdi0024/ProxyCollector/master/sub/proxies.txt",
    
    # 3. Актуальный сборщик VLESS (включает Reality и другие современные протоколы)
    "https://raw.githubusercontent.com/iboxz/free-v2ray-collector/main/vless.txt",
    
    # 4. Резервный агрегатор с частыми обновлениями (каждые 15 минут)
    "https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/Configs.txt",
]

OUTPUT_FILE = "vless_configs.txt"
TIMEOUT = 4  # Чуть увеличили таймаут для REALITY-серверов

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
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            sorted_query,
            '' 
        ))
        return canonical.lower()
    except Exception:
        return clean_config_string(config).split('#')[0].lower()

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
        response = requests.get(url, timeout=20, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        if response.status_code != 200:
            print(f"[WARN] Источник вернул код {response.status_code}")
            return configs
            
        text = response.text
        
        # Если это не прямой текст, пробуем декодировать Base64
        if "githubusercontent" in url and not text.startswith('vless://'):
            text = try_decode_base64(text)
            
        # Ищем все vless:// ссылки
        # found = re.findall(r'vless://[^\s<>"\']+', text)
        found = re.findall(r'(?:vless|vmess|trojan|hysteria2|ss)://[^\s<>"\']+', text)
        
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
    print("СБОР VLESS КОНФИГУРАЦИЙ (ОПТИМИЗИРОВАНО ДЛЯ РФ / ЙОТА)")
    print("Приоритет: VLESS + REALITY (обход DPI)")
    print("=" * 70)
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("# VLESS конфигурации (Reality-optimized)\n")

    all_configs = []
    source_stats = []
    
    for url in SOURCE_URLS:
        configs = fetch_and_extract(url)
        source_stats.append((url, len(configs)))
        all_configs.extend(configs)
        
    print("\n" + "=" * 70)
    print("ГЛУБОКАЯ ДЕДУПЛИКАЦИЯ...")
    
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
            
    print(f"Всего собрано сырых ссылок: {len(all_configs)}")
    print(f"Удалено дубликатов: {duplicates_removed}")
    print(f"Осталось уникальных профилей: {len(final_configs)}")
    print("=" * 70)

    if not final_configs:
        print("\n[CRITICAL] Не найдено ни одной ссылки vless://")
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# ОШИБКА: Источники не содержат vless:// конфигураций\n")
        return

    print("\nПроверка доступности серверов (TCP-рукопожатие)...")
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
            print("[WARN] TCP-проверка не прошла ни один сервер (возможны ограничения GitHub Actions).")
            print("💡 Сохраняем ВСЕ уникальные конфигурации. Проверьте их в NekoBox вручную.")
            for config in final_configs:
                f.write(config + '\n')
            
    print(f"\n🎉 Файл {OUTPUT_FILE} успешно обновлен!")

if __name__ == "__main__":
    main()
