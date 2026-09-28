"""ALFRED_TEST_ENCRYPT=1 pytest — прогнать все тесты с включённым шифрованием базы."""

import os

import pytest

from alfred.database import db as dbmod

if os.getenv("ALFRED_TEST_ENCRYPT") == "1":
    from alfred.database.crypto import new_key
    _KEY = new_key()
    _orig = dbmod.Database.__init__

    def _init(self, path, key=None):
        _orig(self, path, key or _KEY)

    dbmod.Database.__init__ = _init
