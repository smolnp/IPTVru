#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Автоматический обновлятор IPTV плейлистов для smolnp/IPTVru
Проверяет работоспособность каналов и обновляет все плейлисты
"""

import os
import sys
import json
import time
import re
import hashlib
import pickle
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set, Tuple
from urllib.parse import urlparse
import concurrent.futures

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============== КОНФИГУРАЦИЯ ==============
CONFIG = {
    'max_workers': 50,              # Максимум потоков для проверки
    'check_timeout': 5,             # Таймаут проверки канала (сек)
    'request_timeout': 15,          # Таймаут загрузки плейлиста
    'batch_size': 100,              # Размер пакета для проверки
    'min_working_channels': 10,     # Минимум рабочих каналов в плейлисте (общий)
    'save_history': True,           # Сохранять историю изменений
    'max_history_days': 30,         # Хранить историю 30 дней
    'cache_hours': 6,               # Кэширование результатов проверки
}

# Полный список плейлистов из репозитория smolnp/IPTVru
PLAYLISTS = {
    'main': 'IPTVru.m3u',           # Основной плейлист
    'stable': 'IPTVstable.m3u8',    # Стабильная версия
    'mirror': 'IPTVmir.m3u8',       # Зеркало
    'xxx': 'IPTVххх.m3u',           # Взрослые каналы (18+)
    'radio': 'IPTVradio.m3u',       # Радиостанции
}

# Настройки для каждого плейлиста
PLAYLIST_SETTINGS = {
    'IPTVru.m3u': {
        'name': 'Основной плейлист',
        'min_working': 50,
        'description': 'Все каналы'
    },
    'IPTVstable.m3u8': {
        'name': 'Стабильная версия',
        'min_working': 30,
        'description': 'Проверенные стабильные каналы'
    },
    'IPTVmir.m3u8': {
        'name': 'Зеркало',
        'min_working': 30,
        'description': 'Альтернативные источники'
    },
    'IPTVххх.m3u': {
        'name': 'Взрослые каналы',
        'min_working': 5,
        'description': 'Каналы для взрослых (18+)',
        'adult': True
    },
    'IPTVradio.m3u': {
        'name': 'Радиостанции',
        'min_working': 10,
        'description': 'Интернет-радио',
        'radio': True
    },
}

HISTORY_DIR = 'history'
LOGS_DIR = 'logs'
REPORTS_DIR = 'reports'

# ============== ВСПОМОГАТЕЛЬНЫЕ КЛАССЫ ==============

class Logger:
    """Простое логирование"""
    def __init__(self):
        os.makedirs(LOGS_DIR, exist_ok=True)
        self.log_file = os.path.join(LOGS_DIR, f"update_{datetime.now().strftime('%Y%m%d')}.log")
    
    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level}] {message}"
        print(log_line)
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(log_line + '\n')
        except:
            pass
    
    def info(self, msg): self.log(msg, "INFO")
    def warning(self, msg): self.log(msg, "WARNING")
    def error(self, msg): self.log(msg, "ERROR")

logger = Logger()


class UserAgentRotator:
    """Ротация User-Agent"""
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0",
    ]
    
    @classmethod
    def get(cls): return random.choice(cls.USER_AGENTS)


class CacheManager:
    """Кэширование результатов проверки"""
    def __init__(self, cache_hours=6):
        self.cache_dir = ".cache_checker"
        self.cache_hours = cache_hours
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def _get_key(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()
    
    def get(self, url: str) -> Optional[Tuple[bool, float]]:
        key = self._get_key(url)
        path = os.path.join(self.cache_dir, f"{key}.cache")
        if os.path.exists(path):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                if datetime.now() - mtime < timedelta(hours=self.cache_hours):
                    with open(path, 'rb') as f:
                        return pickle.load(f)
            except: pass
        return None
    
    def set(self, url: str, status: bool, response_time: float):
        key = self._get_key(url)
        path = os.path.join(self.cache_dir, f"{key}.cache")
        try:
            with open(path, 'wb') as f:
                pickle.dump((status, response_time), f)
        except: pass
    
    def clear_old(self):
        """Очистка старого кэша"""
        try:
            for f in os.listdir(self.cache_dir):
                path = os.path.join(self.cache_dir, f)
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                if datetime.now() - mtime > timedelta(days=7):
                    os.remove(path)
        except: pass


class PlaylistParser:
    """Парсер M3U плейлистов"""
    
    @staticmethod
    def parse(content: str, source_name: str = "") -> List[Dict]:
        """Парсинг M3U в список каналов"""
        channels = []
        lines = content.split('\n')
        i, n = 0, len(lines)
        
        while i < n:
            line = lines[i].strip()
            if line.startswith('#EXTINF:'):
                channel = {
                    'name': '',
                    'url': '',
                    'group': '',
                    'tvg_id': '',
                    'tvg_logo': '',
                    'source': source_name
                }
                
                # Извлекаем название
                if ',' in line:
                    parts = line.split(',')
                    if len(parts) > 1:
                        raw_name = ','.join(parts[1:]).strip()
                        # Очистка названия
                        raw_name = re.sub(r'\(\s*(?:720|1080|480|2160|4K|FHD|HD)\s*[pPi]?\s*\)', '', raw_name, flags=re.I)
                        raw_name = re.sub(r'\s+', ' ', raw_name).strip()
                        channel['name'] = raw_name
                
                # Извлекаем group
                group_match = re.search(r'group-title="([^"]*)"', line)
                if group_match:
                    channel['group'] = group_match.group(1)
                
                # Извлекаем tvg-id
                tvg_match = re.search(r'tvg-id="([^"]*)"', line)
                if tvg_match:
                    channel['tvg_id'] = tvg_match.group(1)
                
                # Извлекаем logo
                logo_match = re.search(r'tvg-logo="([^"]*)"', line)
                if logo_match:
                    channel['tvg_logo'] = logo_match.group(1)
                
                # Ищем URL
                j = i + 1
                while j < n and j < i + 5:
                    next_line = lines[j].strip()
                    if next_line and not next_line.startswith('#'):
                        if next_line.startswith(('http://', 'https://')):
                            channel['url'] = next_line
                        break
                    j += 1
                
                if channel['name'] and channel['url'] and len(channel['name']) >= 2:
                    channels.append(channel)
                i = j
            else:
                i += 1
        
        return channels
    
    @staticmethod
    def parse_file(filepath: str) -> List[Dict]:
        """Парсинг файла плейлиста"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            return PlaylistParser.parse(content, os.path.basename(filepath))
        except Exception as e:
            logger.error(f"Ошибка парсинга {filepath}: {e}")
            return []
    
    @staticmethod
    def save_playlist(channels: List[Dict], filepath: str):
        """Сохранение плейлиста в файл"""
        try:
            # Получаем настройки для этого плейлиста
            settings = PLAYLIST_SETTINGS.get(filepath, {})
            playlist_name = settings.get('name', os.path.basename(filepath))
            description = settings.get('description', '')
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('#EXTM3U\n')
                f.write(f'# Playlist: {playlist_name}\n')
                f.write(f'# Updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                f.write(f'# Channels: {len(channels)}\n')
                if description:
                    f.write(f'# Description: {description}\n')
                if settings.get('adult'):
                    f.write('# Adult content: 18+\n')
                if settings.get('radio'):
                    f.write('# Type: Radio streams\n')
                f.write('#\n\n')
                
                for ch in channels:
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
            
            logger.info(f"Сохранён плейлист: {filepath} ({len(channels)} каналов)")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения {filepath}: {e}")
            return False


class StreamChecker:
    """Проверка работоспособности потоков"""
    
    def __init__(self, timeout: int = 5, max_workers: int = 50):
        self.timeout = timeout
        self.max_workers = max_workers
        self.cache = CacheManager()
        self._stop = False
    
    def check_url(self, url: str) -> Tuple[bool, float]:
        """Проверка одного URL"""
        if not url or len(url) < 10:
            return False, 0.0
        
        # Проверка кэша
        cached = self.cache.get(url)
        if cached:
            return cached
        
        try:
            session = requests.Session()
            headers = {'User-Agent': UserAgentRotator.get(), 'Connection': 'close'}
            session.headers.update(headers)
            
            start = time.time()
            
            # Сначала HEAD запрос
            try:
                resp = session.head(url, timeout=self.timeout, allow_redirects=True, verify=False)
                if resp.status_code in [200, 206, 301, 302, 403]:
                    response_time = time.time() - start
                    self.cache.set(url, True, response_time)
                    return True, response_time
            except:
                pass
            
            # Если HEAD не сработал, пробуем GET с небольшим буфером
            try:
                resp = session.get(url, timeout=self.timeout, stream=True, verify=False)
                for chunk in resp.iter_content(chunk_size=512):
                    if resp.status_code in [200, 206]:
                        response_time = time.time() - start
                        session.close()
                        self.cache.set(url, True, response_time)
                        return True, response_time
                    break
                session.close()
            except:
                pass
            
            self.cache.set(url, False, self.timeout)
            return False, self.timeout
            
        except Exception as e:
            self.cache.set(url, False, self.timeout)
            return False, self.timeout
    
    def check_channels(self, channels: List[Dict], progress_callback=None) -> List[Dict]:
        """Массовая проверка каналов"""
        results = []
        total = len(channels)
        checked = 0
        working = 0
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_channel = {
                executor.submit(self.check_url, ch['url']): ch 
                for ch in channels
            }
            
            for future in concurrent.futures.as_completed(future_to_channel):
                channel = future_to_channel[future]
                checked += 1
                try:
                    is_working, response_time = future.result()
                    if is_working:
                        working += 1
                        channel['working'] = True
                        channel['response_time'] = response_time
                        results.append(channel)
                except:
                    pass
                
                if progress_callback and checked % 50 == 0:
                    progress_callback(checked, total, working)
        
        logger.info(f"Проверка завершена: {working}/{total} рабочих каналов")
        return results


class HistoryManager:
    """Управление историей изменений"""
    
    def __init__(self, history_dir: str = HISTORY_DIR):
        self.history_dir = history_dir
        os.makedirs(history_dir, exist_ok=True)
    
    def save_snapshot(self, playlist_name: str, channels: List[Dict]):
        """Сохраняет снимок плейлиста"""
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = playlist_name.replace('.', '_').replace('/', '_')
        snapshot_file = os.path.join(self.history_dir, f"{safe_name}_{date_str}.json")
        
        data = {
            'timestamp': datetime.now().isoformat(),
            'playlist': playlist_name,
            'channel_count': len(channels),
            'channels': [
                {'name': ch['name'], 'url': ch['url'], 'group': ch.get('group', '')}
                for ch in channels
            ]
        }
        
        try:
            with open(snapshot_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Сохранён снимок: {snapshot_file}")
        except Exception as e:
            logger.error(f"Ошибка сохранения снимка: {e}")
    
    def clean_old(self, max_days: int = 30):
        """Удаляет старые снимки"""
        try:
            cutoff = datetime.now() - timedelta(days=max_days)
            for f in os.listdir(self.history_dir):
                if f.endswith('.json'):
                    filepath = os.path.join(self.history_dir, f)
                    mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if mtime < cutoff:
                        os.remove(filepath)
                        logger.info(f"Удалён старый снимок: {f}")
        except Exception as e:
            logger.error(f"Ошибка очистки истории: {e}")


class PlaylistUpdater:
    """Основной класс обновления плейлистов"""
    
    def __init__(self):
        self.checker = StreamChecker(
            timeout=CONFIG['check_timeout'],
            max_workers=CONFIG['max_workers']
        )
        self.history = HistoryManager()
        self.stats = {}
    
    def update_playlist(self, filepath: str, save_snapshot: bool = True) -> Dict:
        """Обновление одного плейлиста"""
        logger.info(f"\n{'='*50}")
        logger.info(f"Обработка плейлиста: {filepath}")
        
        settings = PLAYLIST_SETTINGS.get(filepath, {})
        playlist_name = settings.get('name', filepath)
        min_working = settings.get('min_working', CONFIG['min_working_channels'])
        
        logger.info(f"Название: {playlist_name}")
        logger.info(f"Мин. каналов: {min_working}")
        logger.info(f"{'='*50}")
        
        stats = {
            'file': filepath,
            'name': playlist_name,
            'original_count': 0,
            'working_count': 0,
            'removed_count': 0,
            'start_time': datetime.now()
        }
        
        # 1. Парсим текущий плейлист
        channels = PlaylistParser.parse_file(filepath)
        stats['original_count'] = len(channels)
        logger.info(f"Загружено каналов: {len(channels)}")
        
        if len(channels) == 0:
            logger.warning(f"Плейлист {filepath} пуст, пропускаем")
            return stats
        
        # 2. Сохраняем снимок до изменений
        if save_snapshot and CONFIG['save_history']:
            self.history.save_snapshot(filepath, channels)
        
        # 3. Проверяем работоспособность
        logger.info(f"Начинаем проверку {len(channels)} каналов...")
        
        def progress_cb(checked, total, working):
            if checked % 100 == 0:
                logger.info(f"Прогресс: {checked}/{total} | Рабочих: {working}")
        
        working_channels = self.checker.check_channels(channels, progress_cb)
        stats['working_count'] = len(working_channels)
        stats['removed_count'] = stats['original_count'] - stats['working_count']
        
        logger.info(f"Результат: {stats['working_count']} рабочих, {stats['removed_count']} нерабочих")
        
        # 4. Сохраняем обновлённый плейлист
        if working_channels and len(working_channels) >= min_working:
            PlaylistParser.save_playlist(working_channels, filepath)
            stats['success'] = True
            logger.info(f"✅ Плейлист обновлён: {stats['working_count']} каналов")
        else:
            logger.warning(f"❌ Слишком мало рабочих каналов ({len(working_channels)} < {min_working}), пропускаем сохранение")
            stats['success'] = False
        
        stats['end_time'] = datetime.now()
        stats['duration'] = (stats['end_time'] - stats['start_time']).total_seconds()
        
        return stats
    
    def update_all(self) -> Dict:
        """Обновление всех плейлистов"""
        logger.info("\n" + "="*60)
        logger.info("ЗАПУСК АВТОМАТИЧЕСКОГО ОБНОВЛЕНИЯ ПЛЕЙЛИСТОВ")
        logger.info(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("="*60)
        
        total_stats = {
            'start_time': datetime.now(),
            'playlists': {},
            'total_original': 0,
            'total_working': 0,
            'total_removed': 0
        }
        
        for key, filename in PLAYLISTS.items():
            if os.path.exists(filename):
                stats = self.update_playlist(filename)
                total_stats['playlists'][key] = stats
                total_stats['total_original'] += stats.get('original_count', 0)
                total_stats['total_working'] += stats.get('working_count', 0)
                total_stats['total_removed'] += stats.get('removed_count', 0)
            else:
                logger.warning(f"Файл не найден: {filename}")
                # Создаём пустой плейлист если его нет
                PlaylistParser.save_playlist([], filename)
                logger.info(f"Создан пустой плейлист: {filename}")
        
        total_stats['end_time'] = datetime.now()
        total_stats['duration'] = (total_stats['end_time'] - total_stats['start_time']).total_seconds()
        
        # Сохраняем отчёт
        self.save_report(total_stats)
        
        # Очищаем старую историю
        if CONFIG['save_history']:
            self.history.clean_old(CONFIG['max_history_days'])
        
        # Очищаем кэш
        self.checker.cache.clear_old()
        
        return total_stats
    
    def save_report(self, stats: Dict):
        """Сохраняет отчёт об обновлении"""
        os.makedirs(REPORTS_DIR, exist_ok=True)
        
        report_file = os.path.join(REPORTS_DIR, f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        
        report = {
            'timestamp': datetime.now().isoformat(),
            'duration_seconds': stats['duration'],
            'summary': {
                'total_original': stats['total_original'],
                'total_working': stats['total_working'],
                'total_removed': stats['total_removed'],
                'success_rate': f"{(stats['total_working']/stats['total_original']*100):.1f}%" if stats['total_original'] > 0 else "0%"
            },
            'playlists': {}
        }
        
        for key, playlist_stats in stats['playlists'].items():
            report['playlists'][key] = {
                'name': playlist_stats.get('name', key),
                'file': playlist_stats.get('file', ''),
                'original_count': playlist_stats.get('original_count', 0),
                'working_count': playlist_stats.get('working_count', 0),
                'removed_count': playlist_stats.get('removed_count', 0),
                'success': playlist_stats.get('success', False),
                'duration_seconds': playlist_stats.get('duration', 0)
            }
        
        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"Отчёт сохранён: {report_file}")
        except Exception as e:
            logger.error(f"Ошибка сохранения отчёта: {e}")
        
        # Также выводим краткую статистику в лог
        logger.info("\n" + "="*50)
        logger.info("ИТОГОВАЯ СТАТИСТИКА:")
        logger.info(f"Всего каналов: {stats['total_original']}")
        logger.info(f"Рабочих: {stats['total_working']}")
        logger.info(f"Удалено: {stats['total_removed']}")
        logger.info(f"Эффективность: {(stats['total_working']/stats['total_original']*100):.1f}%" if stats['total_original'] > 0 else "0%")
        logger.info(f"Время выполнения: {stats['duration']:.2f} сек")
        
        # Статистика по каждому плейлисту
        logger.info("\nДетализация по плейлистам:")
        for key, playlist_stats in stats['playlists'].items():
            status = "✅" if playlist_stats.get('success') else "❌"
            logger.info(f"  {status} {playlist_stats.get('name', key)}: {playlist_stats.get('working_count', 0)}/{playlist_stats.get('original_count', 0)}")
        
        logger.info("="*50)


def create_readme_if_not_exists():
    """Создаёт README если его нет"""
    readme_path = "README.md"
    if not os.path.exists(readme_path):
        content = """# IPTVru Playlists

Автоматически обновляемые IPTV плейлисты.

## 📺 Плейлисты

| Файл | Описание | Ссылка |
|------|----------|--------|
| `IPTVru.m3u` | Основной плейлист со всеми каналами | [Скачать](https://raw.githubusercontent.com/smolnp/IPTVru/main/IPTVru.m3u) |
| `IPTVstable.m3u8` | Стабильная версия с проверенными каналами | [Скачать](https://raw.githubusercontent.com/smolnp/IPTVru/main/IPTVstable.m3u8) |
| `IPTVmir.m3u8` | мировые каналы | [Скачать](https://raw.githubusercontent.com/smolnp/IPTVru/main/IPTVmir.m3u8) |
| `IPTVххх.m3u` | Тестовый плейлист | [Скачать](https://raw.githubusercontent.com/smolnp/IPTVru/main/IPTVххх.m3u) |
| `IPTVradio.m3u` | Интернет-радиостанции | [Скачать](https://raw.githubusercontent.com/smolnp/IPTVru/main/IPTVradio.m3u) |

## 🔄 Обновление

Плейлисты автоматически проверяются и обновляются **2 раза в сутки** (00:00 и 12:00 UTC).

### Что происходит при обновлении:
- ✅ Проверка работоспособности всех каналов
- ❌ Удаление неработающих каналов
- 📊 Сохранение истории изменений
- 📝 Создание детальных отчётов

## 📊 Статистика

Актуальная статистика доступна в папке [`reports/`](reports/)

## 🚀 Использование

Добавьте ссылку на плейлист в ваш IPTV плеер:
