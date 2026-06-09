#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Быстрая проверка каналов в плейлисте
Запуск: python check_channels.py [file.m3u]
"""

import sys
import time
from update_playlists import PlaylistParser, StreamChecker, logger

def main():
    if len(sys.argv) < 2:
        print("Использование: python check_channels.py <playlist_file.m3u>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    
    print(f"Проверка плейлиста: {filepath}")
    
    # Парсим
    channels = PlaylistParser.parse_file(filepath)
    print(f"Загружено каналов: {len(channels)}")
    
    # Проверяем
    checker = StreamChecker(timeout=5, max_workers=30)
    
    def progress(checked, total, working):
        print(f"\rПрогресс: {checked}/{total} | Рабочих: {working}", end="")
    
    start = time.time()
    working = checker.check_channels(channels, progress)
    duration = time.time() - start
    
    print(f"\n\nРезультат: {len(working)}/{len(channels)} рабочих каналов")
    print(f"Время: {duration:.2f} сек")
    
    if working:
        # Сохраняем проверенную версию
        output = filepath.replace('.m3u', '_checked.m3u').replace('.m3u8', '_checked.m3u8')
        PlaylistParser.save_playlist(working, output, "Checked Playlist")
        print(f"Сохранено: {output}")

if __name__ == "__main__":
    main()