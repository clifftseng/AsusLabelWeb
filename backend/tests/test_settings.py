import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.settings import ensure_env_loaded

def test_ensure_env_loaded_sets_variables(monkeypatch, tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text('CUSTOM_KEY=VALUE\n')
    monkeypatch.delenv('CUSTOM_KEY', raising=False)

    ensure_env_loaded(env_path=env_file, force=True)

    assert os.getenv('CUSTOM_KEY') == 'VALUE'

