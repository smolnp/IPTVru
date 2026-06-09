#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import random
import time
from datetime import datetime
from typing import List, Dict, Tuple
import concurrent.futures
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Конфигурация
CONFIG = {
    'max_workers': 50,
    'check_timeout': 5,
    'min_working_channels': 10,
    'remove_empty_urls': True,
    'remove_invalid_urls': True,
    'remove_duplicates': True,
    'clean_channel_names': True,
    'add_blank_line_after_header': True,
}

# Список плейлистов
PLAYLISTS = {
    'main': 'IPTVru.m3u',
    'stable': 'IPTVstable.m3u8',
    'mirror': 'IPTVmir.m3u8',
    'xxx': 'IPTVххх.m3u',
    'radio': 'IPTVradio.m3u',
}

# Настройки плейлистов
PLAYLIST_SETTINGS = {
    'IPTVru.m3u': {
        'name': 'IPTVru', 
        'min_working': 50,
        'epg_url': 'https://iptvx.one/epg/epg.xml.gz'
    },
    'IPTVstable.m3u8': {
        'name': 'IPTVstable', 
        'min_working': 30,
        'epg_url': 'https://iptvx.one/epg/epg.xml.gz'
    },
    'IPTVmir.m3u8': {
        'name': 'IPTVmir', 
        'min_working': 30,
        'epg_url': 'https://iptvx.one/epg/epg.xml.gz'
    },
    'IPTVххх.m3u': {
        'name': 'IPTVxxx', 
        'min_working': 5, 
        'adult': True
    },
    'IPTVradio.m3u': {
        'name': 'IPTVradio', 
        'min_working': 10, 
        'radio': True
    },
}

def log(msg):
    """Простой вывод в консоль"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")

class UserAgentRotator:
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]
    @classmethod
    def get(cls): return random.choice(cls.USER_AGENTS)

class UrlValidator:
    @staticmethod
    def is_valid(url: str) -> bool:
        """Проверка URL - более либеральная"""
        if not url or not isinstance(url, str):
            return False
        
        url = url.strip()
        if len(url) < 8:
            return False
        
        # Должна начинаться с http:// или https://
        if not (url.startswith('http://') or url.startswith('https://')):
            return False
        
        # Проверка на явный мусор
        invalid_patterns = [r'^#', r'^null$', r'^undefined$', r'^None$', r'^\s*$']
        for pattern in invalid_patterns:
            if re.match(pattern, url, re.IGNORECASE):
                return False
        
        # Базовые недопустимые символы
        invalid_chars = ['\n', '\r', '\t', '\x00']
        for char in invalid_chars:
            if char in url:
                return False
        
        return True
    
    @staticmethod
    def is_empty(url: str) -> bool:
        """Проверка на пустую ссылку"""
        if not url:
            return True
        url = url.strip()
        empty_patterns = [r'^#.*$', r'^\s*$', r'^null$', r'^undefined$', r'^None$']
        for pattern in empty_patterns:
            if re.match(pattern, url, re.IGNORECASE):
                return True
        return False

class ChannelCleaner:
    @staticmethod
    def clean_channel_name(name: str) -> str:
        """Очистка названия канала"""
        if not name:
            return ""
        
        # Удаляем техническую информацию, но сохраняем суть
        patterns_to_remove = [
            r'\(\s*(?:480|360)\s*[pPi]?\s*\)',  # только низкое качество
            r'-\s*(?:COPY|COPYRIGHT)\s*',
        ]
        for pattern in patterns_to_remove:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)
        
        # Удаляем лишние пробелы
        name = re.sub(r'\s+', ' ', name)
        name = name.strip()
        
        return name if name else "Unknown Channel"

class PlaylistParser:
    @staticmethod
    def parse(content, source_name=""):
        """Парсинг M3U с поддержкой EXTVLCOPT и других опций"""
        channels = []
        lines = content.split('\n')
        i = 0
        n = len(lines)
        
        while i < n:
            line = lines[i].strip()
            
            # Пропускаем пустые строки и комментарии (кроме EXTINF)
            if not line or (line.startswith('#') and not line.startswith('#EXTINF')):
                i += 1
                continue
            
            # Нашли строку канала
            if line.startswith('#EXTINF:'):
                channel = {
                    'name': '',
                    'url': '',
                    'group': '',
                    'tvg_id': '',
                    'tvg_logo': '',
                    'source': source_name
                }
                
                # Извлекаем название (всё после последней запятой)
                if ',' in line:
                    # Разделяем по запятой, но учитываем что в tvg-id тоже может быть запятая
                    parts = line.split(',')
                    if len(parts) > 1:
                        # Название - это всё, что после последней запятой
                        raw_name = parts[-1].strip()
                        channel['name'] = raw_name
                
                # Извлекаем tvg-id
                tvg_match = re.search(r'tvg-id="([^"]*)"', line)
                if tvg_match:
                    channel['tvg_id'] = tvg_match.group(1)
                
                # Извлекаем tvg-logo
                logo_match = re.search(r'tvg-logo="([^"]*)"', line)
                if logo_match:
                    channel['tvg_logo'] = logo_match.group(1)
                
                # Извлекаем group-title
                group_match = re.search(r'group-title="([^"]*)"', line)
                if group_match:
                    channel['group'] = group_match.group(1)
                
                # Ищем URL (может быть на следующей строке или после опций)
                j = i + 1
                url_found = None
                
                while j < n and j < i + 10:
                    next_line = lines[j].strip()
                    
                    # Пропускаем пустые строки
                    if not next_line:
                        j += 1
                        continue
                    
                    # Пропускаем EXTVLCOPT и другие опции VLC
                    if next_line.startswith('#EXTVLCOPT:'):
                        j += 1
                        continue
                    
                    # Если нашли URL
                    if next_line.startswith(('http://', 'https://')):
                        url_found = next_line
                        break
                    
                    # Если нашли строку которая не начинается с # - возможно это URL
                    if not next_line.startswith('#'):
                        # Проверяем, похоже ли на URL
                        if re.match(r'^https?://', next_line) or '.' in next_line:
                            url_found = next_line
                            break
                    
                    j += 1
                
                if url_found:
                    channel['url'] = url_found
                    # Перемещаем указатель на строку после URL
                    i = j + 1
                else:
                    i += 1
                
                # Добавляем канал только если есть и название, и URL
                if channel['name'] and channel['url']:
                    channels.append(channel)
                else:
                    log(f"Пропущен канал: name='{channel['name']}', url='{channel['url']}'")
            else:
                i += 1
        
        return channels
    
    @staticmethod
    def parse_file(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            channels = PlaylistParser.parse(content, os.path.basename(filepath))
            log(f"Парсинг {filepath}: найдено {len(channels)} каналов")
            return channels
        except Exception as e:
            log(f"Ошибка парсинга {filepath}: {e}")
            return []
    
    @staticmethod
    def save_playlist(channels, filepath):
        try:
            settings = PLAYLIST_SETTINGS.get(filepath, {})
            name = settings.get('name', os.path.basename(filepath))
            epg_url = settings.get('epg_url', '')
            
            with open(filepath, 'w', encoding='utf-8') as f:
                # Заголовок плейлиста
                if epg_url:
                    f.write(f'#EXTM3U url-tvg="{epg_url}"\n')
                else:
                    f.write('#EXTM3U\n')
                
                # Метаданные
                f.write(f'#PLAYLIST:{name}\n')
                f.write(f'#UPDATED:{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                f.write(f'#CHANNELS:{len(channels)}\n')
                
                # Пустая строка для разделения
                if CONFIG.get('add_blank_line_after_header', True):
                    f.write('\n')
                
                # Сохраняем каналы
                for ch in channels:
                    # Очищаем название
                    if CONFIG['clean_channel_names']:
                        ch['name'] = ChannelCleaner.clean_channel_name(ch['name'])
                    
                    # Собираем атрибуты
                    attrs = []
                    if ch.get('tvg_id'):
                        attrs.append(f'tvg-id="{ch["tvg_id"]}"')
                    if ch.get('tvg_logo'):
                        attrs.append(f'tvg-logo="{ch["tvg_logo"]}"')
                    if ch.get('group'):
                        attrs.append(f'group-title="{ch["group"]}"')
                    
                    attr_str = ' '.join(attrs)
                    if attr_str:
                        f.write(f'#EXTINF:{attr_str} ,{ch["name"]}\n')
                    else:
                        f.write(f'#EXTINF:-1 ,{ch["name"]}\n')
                    
                    f.write(f'{ch["url"]}\n')
            
            log(f"Сохранён: {filepath} ({len(channels)} каналов)")
            return True
        except Exception as e:
            log(f"Ошибка сохранения {filepath}: {e}")
            return False

class StreamChecker:
    def __init__(self, timeout=5, max_workers=50):
        self.timeout = timeout
        self.max_workers = max_workers
    
    def check_url(self, url):
        """Проверка URL - более щадящая"""
        if not url or len(url) < 8:
            return False
        
        try:
            session = requests.Session()
            headers = {
                'User-Agent': UserAgentRotator.get(),
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'close'
            }
            session.headers.update(headers)
            
            start = time.time()
            
            # Пробуем HEAD запрос
            try:
                resp = session.head(url, timeout=self.timeout, allow_redirects=True, verify=False)
                if resp.status_code in [200, 206, 301, 302, 304, 403, 404]:
                    # 404 тоже может быть рабочим (поток с защитой)
                    if resp.status_code != 404 or 'm3u8' in url or 'mpd' in url:
                        return True
            except:
                pass
            
            # Пробуем GET с маленьким буфером
            try:
                resp = session.get(url, timeout=self.timeout, stream=True, verify=False)
                for chunk in resp.iter_content(chunk_size=512):
                    if resp.status_code in [200, 206, 302]:
                        session.close()
                        return True
                    break
                session.close()
            except:
                pass
            
            return False
        except:
            return False
    
    def check_channels(self, channels):
        """Массовая проверка каналов"""
        results = []
        total = len(channels)
        working = 0
        
        log(f"Начинаем проверку {total} каналов...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.check_url, ch['url']): ch for ch in channels}
            
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                ch = futures[future]
                try:
                    is_working = future.result()
                    if is_working:
                        working += 1
                        results.append(ch)
                except:
                    pass
                
                if (i + 1) % 20 == 0:
                    log(f"Прогресс: {i+1}/{total} | Рабочих: {working}")
        
        log(f"Проверка завершена: {working}/{total} рабочих каналов")
        return results

class PlaylistUpdater:
    def __init__(self):
        self.checker = StreamChecker(CONFIG['check_timeout'], CONFIG['max_workers'])
    
    def clean_channels(self, channels: List[Dict]) -> Tuple[List[Dict], Dict]:
        """Очистка каналов от мусора (без агрессивного удаления)"""
        original_count = len(channels)
        cleaned = []
        removed_stats = {'empty_url': 0, 'invalid_url': 0, 'invalid_name': 0, 'duplicate_url': 0}
        seen_urls = set()
        
        for ch in channels:
            url = ch.get('url', '')
            
            # Проверка на пустую ссылку
            if CONFIG['remove_empty_urls'] and UrlValidator.is_empty(url):
                removed_stats['empty_url'] += 1
                continue
            
            # Проверка на валидность URL (только явно невалидные)
            if CONFIG['remove_invalid_urls'] and not UrlValidator.is_valid(url):
                # Пропускаем только если это точно не URL
                if url and not (url.startswith('http://') or url.startswith('https://')):
                    removed_stats['invalid_url'] += 1
                    continue
            
            # Проверка названия
            name = ch.get('name', '')
            if not name or len(name.strip()) < 1:
                removed_stats['invalid_name'] += 1
                continue
            
            # Проверка на дубликаты URL
            if CONFIG['remove_duplicates'] and url in seen_urls:
                removed_stats['duplicate_url'] += 1
                continue
            
            seen_urls.add(url)
            
            # Очищаем название (но не удаляем важную информацию)
            if CONFIG['clean_channel_names']:
                ch['name'] = ChannelCleaner.clean_channel_name(ch['name'])
            
            cleaned.append(ch)
        
        total_removed = sum(removed_stats.values())
        if total_removed > 0:
            log(f"Очистка: удалено {total_removed} каналов (пустых: {removed_stats['empty_url']}, невалидных: {removed_stats['invalid_url']}, без названий: {removed_stats['invalid_name']}, дубликатов: {removed_stats['duplicate_url']})")
        
        return cleaned, removed_stats
    
    def update_playlist(self, filepath):
        log(f"\n{'='*50}")
        log(f"Обработка: {filepath}")
        
        settings = PLAYLIST_SETTINGS.get(filepath, {})
        min_working = settings.get('min_working', CONFIG['min_working_channels'])
        
        if not os.path.exists(filepath):
            log(f"Файл не найден: {filepath}")
            PlaylistParser.save_playlist([], filepath)
            return {'file': filepath, 'original_count': 0, 'working_count': 0}
        
        # Парсим плейлист
        channels = PlaylistParser.parse_file(filepath)
        original_count = len(channels)
        log(f"Загружено: {original_count} каналов")
        
        if original_count == 0:
            return {'file': filepath, 'original_count': 0, 'working_count': 0}
        
        # Очищаем от мусора
        cleaned_channels, _ = self.clean_channels(channels)
        cleaned_count = len(cleaned_channels)
        
        if cleaned_count == 0:
            log("После очистки не осталось каналов")
            return {'file': filepath, 'original_count': original_count, 'working_count': 0}
        
        # Проверяем работоспособность
        working_channels = self.checker.check_channels(cleaned_channels)
        working_count = len(working_channels)
        
        log(f"Результат: {working_count} рабочих каналов из {original_count}")
        
        # Сохраняем если достаточно каналов
        if working_count >= min_working:
            PlaylistParser.save_playlist(working_channels, filepath)
            log(f"✅ Сохранено {working_count} каналов")
        else:
            # Если мало рабочих, сохраняем всё равно (лучше чем ничего)
            if working_count > 0:
                PlaylistParser.save_playlist(working_channels, filepath)
                log(f"⚠️ Сохранено только {working_count} каналов (меньше минимума {min_working})")
            else:
                log(f"❌ Нет рабочих каналов, плейлист не обновлён")
        
        return {'file': filepath, 'original_count': original_count, 'working_count': working_count}
    
    def update_all(self):
        log(f"\n{'='*60}")
        log("ЗАПУСК ОБНОВЛЕНИЯ ПЛЕЙЛИСТОВ")
        log(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"{'='*60}")
        
        results = []
        total_original = 0
        total_working = 0
        
        for key, filename in PLAYLISTS.items():
            stats = self.update_playlist(filename)
            results.append(stats)
            total_original += stats.get('original_count', 0)
            total_working += stats.get('working_count', 0)
        
        # Итоговая статистика
        log(f"\n{'='*50}")
        log("ИТОГОВАЯ СТАТИСТИКА:")
        log(f"Всего каналов: {total_original}")
        log(f"Рабочих каналов: {total_working}")
        if total_original > 0:
            log(f"Эффективность: {(total_working/total_original*100):.1f}%")
        log(f"{'='*50}")
        
        return results

def main():
    log("Запуск обновления плейлистов...")
    
    updater = PlaylistUpdater()
    results = updater.update_all()
    
    if results and any(r.get('working_count', 0) > 0 for r in results):
        log("✅ Обновление завершено успешно!")
        return 0
    else:
        log("❌ Обновление завершено с ошибками")
        return 1

if __name__ == "__main__":
    sys.exit(main())
