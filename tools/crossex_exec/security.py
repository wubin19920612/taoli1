"""Secrets enter only through a private file; raw remote errors never leave transport."""
import os
import re
import stat
import threading
from collections import deque
from dataclasses import dataclass, field


class Redactor:
    def __init__(self):
        self.values = set()
        self.signatures = deque(maxlen=2048)
        self.lock = threading.RLock()

    def register(self, *values):
        with self.lock:
            self.values.update(v for v in values if v)

    def signature(self, value):
        with self.lock:
            self.signatures.append(value)

    def clean(self, value):
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()
                    if not any(word in k.lower() for word in ('secret','passphrase','signature','api_key','headers'))}
        if isinstance(value, (tuple, list)):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            with self.lock:
                for token in sorted(self.values | set(self.signatures), key=len, reverse=True):
                    value = value.replace(token, '[REDACTED]')
            return value
        return value


REDACTOR = Redactor()


@dataclass(repr=False)
class Credentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(default='', repr=False)

    def __post_init__(self):
        REDACTOR.register(self.key, self.secret, self.passphrase)

    def __repr__(self):
        return 'Credentials(<redacted>)'


def load_env(path):
    """No shell expansion, process-environment fallback, symlink or group access."""
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600:
            raise ValueError('env_requires_regular_file_mode_600')
        if hasattr(os, 'getuid') and st.st_uid != os.getuid():
            raise ValueError('env_owner_mismatch')
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            fd = None
            content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError('env_too_large')
        values = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            name, sep, value = line.partition('=')
            if not sep or not re.fullmatch('[A-Z][A-Z0-9_]*', name) or name in values:
                raise ValueError('invalid_env_file')
            values[name] = value
        REDACTOR.register(*(v for k,v in values.items() if any(s in k for s in ('KEY','SECRET','PASSPHRASE','TOKEN'))))
        return values
    finally:
        if fd is not None:
            os.close(fd)
