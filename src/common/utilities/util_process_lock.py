import os
from pathlib import Path


class ProcessFileLock:
    def __init__(self, path):
        self.path = Path(path)
        self._file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open('a+b')
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() == 0:
            self._file.write(b'0')
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._file.close()
            self._file = None
            raise RuntimeError(f'Another scheduler process holds {self.path}') from error
        return self

    def release(self):
        if not self._file:
            return
        self._file.seek(0)
        if os.name == 'nt':
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()