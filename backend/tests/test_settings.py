import os
from pathlib import Path

from settings import ensure_env_loaded

def test_ensure_env_loaded_sets_variables(monkeypatch, tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text('CUSTOM_KEY=VALUE\n')
    monkeypatch.delenv('CUSTOM_KEY', raising=False)

    ensure_env_loaded(env_path=env_file, force=True)

    assert os.getenv('CUSTOM_KEY') == 'VALUE'

