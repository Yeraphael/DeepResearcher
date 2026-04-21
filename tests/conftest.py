from __future__ import annotations

import sys
import shutil
from pathlib import Path
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"

if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))


TMP_ROOT = ROOT / ".pytest_tmp"
TMP_ROOT.mkdir(exist_ok=True)


@pytest.fixture
def workspace_tmp_path():
    path = TMP_ROOT / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
