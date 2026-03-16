"""
V2: Real-time file watcher for Synology NAS.
Uses filesystem polling (most reliable in Docker on Synology).
Monitors all user Photos directories for new images.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from config import settings

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".tiff", ".tif", ".heic", ".heif",
}


class FileWatcher:
    """
    Polls directories for new image files.
    Optimized: checks directory mtimes first, then scans only changed dirs.
    """

    def __init__(
        self,
        watch_dirs: list[str],
        on_new_file: Callable[[str, str], None],  # (filepath, username)
        poll_interval: int = 30,
    ):
        self.watch_dirs = watch_dirs  # List of (path, username) tuples
        self.on_new_file = on_new_file
        self.poll_interval = poll_interval
        self._running = False
        self._known_files: dict[str, float] = {}  # path -> mtime
        self._dir_mtimes: dict[str, float] = {}  # dir -> last_mtime
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start watching in background."""
        if self._running:
            return
        self._running = True
        logger.info(f"FileWatcher starting, polling every {self.poll_interval}s")
        logger.info(f"Watching: {[d for d, _ in self.watch_dirs]}")

        # Initial scan to populate known files
        await self._initial_scan()

        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self):
        """Stop watching."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("FileWatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "known_files": len(self._known_files),
            "watched_dirs": len(self.watch_dirs),
            "poll_interval": self.poll_interval,
        }

    async def _initial_scan(self):
        """Scan all directories to build initial file inventory."""
        count = 0
        for watch_dir, username in self.watch_dirs:
            for root, dirs, files in os.walk(watch_dir):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d != "@eaDir" and d != "_cleanup"
                ]
                self._dir_mtimes[root] = os.path.getmtime(root)
                for fname in files:
                    if Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                        fpath = os.path.join(root, fname)
                        try:
                            self._known_files[fpath] = os.path.getmtime(fpath)
                            count += 1
                        except OSError:
                            pass
        logger.info(f"FileWatcher initial scan: {count} known files")

    async def _poll_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                await self._check_for_changes()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"FileWatcher poll error: {e}")
                await asyncio.sleep(5)

    async def _check_for_changes(self):
        """Check for new files using directory mtime optimization."""
        new_files = []

        for watch_dir, username in self.watch_dirs:
            # Walk and check directory mtimes
            for root, dirs, files in os.walk(watch_dir):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d != "@eaDir" and d != "_cleanup"
                ]

                try:
                    current_mtime = os.path.getmtime(root)
                except OSError:
                    continue

                # Skip unchanged directories
                last_mtime = self._dir_mtimes.get(root, 0)
                if current_mtime <= last_mtime:
                    continue

                self._dir_mtimes[root] = current_mtime

                # Scan this changed directory for new files
                for fname in files:
                    if Path(fname).suffix.lower() not in IMAGE_EXTENSIONS:
                        continue

                    fpath = os.path.join(root, fname)
                    try:
                        fmtime = os.path.getmtime(fpath)
                    except OSError:
                        continue

                    if fpath not in self._known_files:
                        self._known_files[fpath] = fmtime
                        new_files.append((fpath, username))
                    elif fmtime > self._known_files[fpath]:
                        self._known_files[fpath] = fmtime
                        new_files.append((fpath, username))

        if new_files:
            logger.info(f"FileWatcher detected {len(new_files)} new/modified files")
            for fpath, username in new_files:
                try:
                    self.on_new_file(fpath, username)
                except Exception as e:
                    logger.error(f"Error handling new file {fpath}: {e}")


# Global watcher instance
_watcher: Optional[FileWatcher] = None


def get_watcher() -> Optional[FileWatcher]:
    return _watcher


async def start_watcher(
    on_new_file: Callable[[str, str], None],
    poll_interval: int = 30,
) -> FileWatcher:
    """Start the global file watcher for all NAS users."""
    global _watcher

    if _watcher and _watcher.is_running:
        await _watcher.stop()

    watch_dirs = []
    for username in settings.nas_users:
        photos_dir = f"{settings.homes_mount}/{username}/Photos"
        if os.path.isdir(photos_dir):
            watch_dirs.append((photos_dir, username))
        else:
            logger.warning(f"Watch dir not found: {photos_dir}")

    if not watch_dirs:
        logger.warning("No valid watch directories found")
        return None

    _watcher = FileWatcher(
        watch_dirs=watch_dirs,
        on_new_file=on_new_file,
        poll_interval=poll_interval,
    )
    await _watcher.start()
    return _watcher


async def stop_watcher():
    """Stop the global file watcher."""
    global _watcher
    if _watcher:
        await _watcher.stop()
        _watcher = None
