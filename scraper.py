import re
import socket
import ssl
import base64
import requests
from urllib.parse import urlparse, parse_qsl, urlunparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

# ИСТОЧНИКИ
SOURCE_URLS = [
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    "https://raw.githubusercontent.com/Mahdi0024/ProxyCollector/master/sub/proxies.txt",
    "https://raw.githubusercontent.com/iboxz/free-v2ray-collector/main/vless.txt",
    "https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/Configs.txt",
]

OUTPUT_FILE = "vless_configs.txt"
TIMEOUT = 3

def clean_config_string(config):
    config = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', config)
    config = config.strip()
    config = re.sub(r'\s+', ' ', config)
    return config

def get_canonical_key(config):
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

def parse_vless_config(config):
    """Парсит VLESS-конфиг и извлекает ключевые параметры."""
    try:
        clean = config.replace('vless://', '')
        if '@' not in clean:
            return None
        
        uuid, rest = clean.split('@', 1)
        
        # Извлекаем host:port
        host_port = re.split(r'[/?#]', rest)[0]
        if ':' not in host_port:
            return None
        
        if host_port.startswith('['):
            match = re.match(r'\[([^\]]+)\]:(\d+)', host_port)
            if not match:
                return None
            host, port = match.groups()
        else:
            host, port = host_port.rsplit(':', 1)
        
        # Парсим параметры
        params = {}
        if '?' in rest:
            query = rest.split('?')[1].split('#')[0]
            params = parse_qs(query)
        
        return {
            'uuid': uuid,
            'host': host,
            'port': int(port),
            'security': params.get('security', [''])[0],
            'flow': params.get('flow', [''])[0],
            'type': params.get('type', ['tcp'])[0],
            'sni': params.get('sni', [host])[0],
            'path': params.get('path', ['/'])[0],
        }
    except Exception:
        return None

def check_tcp(host, port, timeout=TIMEOUT):
    """Уровень 1: Проверка TCP-подключения."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
            return True
    except Exception:
        return False

def check_tls(host, port, sni, timeout=TIMEOUT):
    """Уровень 2: Проверка TLS-рукопожатия."""
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=sni) as ssock:
                # Если TLS handshake прошел - сервер живой
                return True
    except Exception:
        return False

def check_http(host, port, path, sni, timeout=TIMEOUT):
    """Уровень 3: Проверка HTTP-ответа для WebSocket-конфигов."""
    try:
        url = f"https://{host}:{port}{path}"
        headers = {
            'Host': sni,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=timeout, verify=False)
        # Любой HTTP-ответ (даже 400/403/404) означает, что сервер живой
        return response.status_code < 500
    except Exception:
        return False

def check_vless_working(config):
    """Многоуровневая проверка работоспособности VLESS-конфига."""
    parsed = parse_vless_config(config)
    if not parsed:
        return False, "parse_error"
    
    host = parsed['host']
    port = parsed['port']
    security = parsed['security']
    sni = parsed['sni']
    path = parsed['path']
    conn_type = parsed['type']
    
    # Уровень 1: TCP
    if not check_tcp(host, port):
        return False, "tcp_failed"
    
    # Уровень 2: TLS (для security=tls или security=reality)
    if security in ['tls', 'reality']:
        if not check_tls(host, port, sni):
            return False, "tls_failed"
    
    # Уровень 3: HTTP (для WebSocket-конфигов с TLS)
    if conn_type == 'ws' and security in ['tls', 'reality']:
        if not check_http(host, port, path, sni):
            return False, "http_failed"
    
    return True, "ok"

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
        if "githubusercontent" in url and not text.startswith('vless://'):
            text = try_decode_base64(text)
            
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
    print("СБОР VLESS КОНФИГУРАЦИЙ (УЛУЧШЕННАЯ ПРОВЕРКА)")
    print("Многоуровневая проверка: TCP -> TLS -> HTTP")
    print("=" * 70)
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("# VLESS конфигурации (проверенные)\n")

    all_configs = []
    for url in SOURCE_URLS:
        all_configs.extend(fetch_and_extract(url))
        
    print("\n" + "=" * 70)
    print("ДЕДУПЛИКАЦИЯ...")
    
    seen_keys = set()
    final_configs = []
    for c in all_configs:
        key = get_canonical_key(c)
        if key not in seen_keys:
            seen_keys.add(key)
            final_configs.append(c)
            
    print(f"Уникальных профилей: {len(final_configs)}")
    print("=" * 70)

    if not final_configs:
        print("\n[CRITICAL] Не найдено ни одной ссылки vless://")
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# ОШИБКА: Источники не содержат vless:// конфигураций\n")
        return

    print("\nМногоуровневая проверка работоспособности...")
    working_configs = []
    stats = {'tcp_failed': 0, 'tls_failed': 0, 'http_failed': 0, 'parse_error': 0, 'ok': 0}
    
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_to_config = {executor.submit(check_vless_working, config): config for config in final_configs}
        for future in as_completed(future_to_config):
            config = future_to_config[future]
            try:
                is_working, reason = future.result()
                stats[reason] = stats.get(reason, 0) + 1
                if is_working:
                    working_configs.append(config)
            except Exception:
                stats['parse_error'] += 1

    print(f"\nСтатистика проверки:")
    print(f"  ✅ Прошли проверку: {stats['ok']}")
    print(f"  ❌ TCP failed: {stats['tcp_failed']}")
    print(f"  ❌ TLS failed: {stats['tls_failed']}")
    print(f"  ❌ HTTP failed: {stats['http_failed']}")
    print(f"  ❌ Parse error: {stats['parse_error']}")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        if working_configs:
            for config in working_configs:
                f.write(config + '\n')
            print(f"\n✅ Сохранено {len(working_configs)} РАБОЧИХ конфигураций.")
        else:
            print("\n[WARN] Ни один конфиг не прошел проверку.")
            print("💡 Сохраняем все уникальные конфиги без проверки.")
            for config in final_configs:
                f.write(config + '\n')
            
    print(f"\n🎉 Файл {OUTPUT_FILE} успешно обновлен!")

if __name__ == "__main__":
    main()
