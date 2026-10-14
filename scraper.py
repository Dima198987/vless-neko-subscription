import re
import socket
import base64
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Список надежных источников, которые не блокируют GitHub Actions.
# Вы можете добавлять сюда любые прямые (raw) ссылки на текстовые файлы или base64-подписки.
SOURCE_URLS = [
    "https://raw.githubusercontent.com/sashalsk/V2Ray/main/V2Config_64base",
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_base64.txt",
    "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/list/00.txt",
    # Пример добавления Telegram-канала через прокси-читалку (без JS и блокировок):
    # "https://r.jina.ai/https://t.me/s/your_favorite_vless_channel"
]

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
    """Скачивает файл, декодирует base64 (если нужно) и извлекает vless:// ссылки."""
    configs = []
    try:
        print(f"Загрузка: {url}")
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        text = response.text
        
        # Попытка декодировать как Base64 (частая практика для подписок)
        try:
            # Убираем пробелы и переносы строк для корректного декодирования
            clean_b64 = re.sub(r'\s+', '', text)
            decoded_bytes = base64.b64decode(clean_b64)
            text = decoded_bytes.decode('utf-8', errors='ignore')
        except Exception:
            # Если не base64, работаем с обычным текстом
            pass

        # Извлекаем все vless:// ссылки
        found = re.findall(r'vless://[^\s<>"\']+', text)
        for c in found:
            c = c.rstrip('.,);]')
            if c not in configs:
                configs.append(c)
                
        print(f"  -> Найдено {len(found)} ссылок (уникальных: {len(configs)})")
    except Exception as e:
        print(f"  -> Ошибка загрузки {url}: {e}")
        
    return configs

def main():
    # 1. Гарантируем создание файла, чтобы git не падал
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("# VLESS конфигурации (агрегированные из открытых источников)\n")

    print("Начинаем сбор конфигураций...")
    all_configs = []
    
    # 2. Сбор данных из всех источников
    for url in SOURCE_URLS:
        all_configs.extend(fetch_and_extract(url))
        
    # 3. Глобальная дедупликация с сохранением порядка
    final_configs = []
    for c in all_configs:
        if c not in final_configs:
            final_configs.append(c)
            
    print(f"\nВсего найдено уникальных конфигураций VLESS: {len(final_configs)}")

    if not final_configs:
        print("Конфигурации не найдены. Проверьте актуальность ссылок в SOURCE_URLS.")
        return

    # 4. Проверка доступности (TCP)
    print("Проверка доступности серверов (TCP)...")
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

    # 5. Сохранение результата
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        if working_configs:
            for config in working_configs:
                f.write(config + '\n')
        else:
            # ВАЖНО: Если GitHub заблокировал исходящие порты, мы все равно сохраним 
            # непроверенный список, чтобы файл не был пустым.
            print("ВНИМАНИЕ: Ни один сервер не прошел TCP-проверку.")
            print("Это часто бывает из-за сетевых ограничений GitHub Actions.")
            print("Сохраняем все найденные конфигурации без проверки.")
            for config in final_configs:
                f.write(config + '\n')
            
    print(f"Результаты сохранены в {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
