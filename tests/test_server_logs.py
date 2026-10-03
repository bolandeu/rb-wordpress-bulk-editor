"""
Логи на сервере: без проекта команды не пишут `logs/` в текущую папку.

На хосте rb-run cwd — чужая (или read-only) папка; файлы логов без проекта
уходят в `platformdirs.user_log_path("wp-bulk")`. С проектом — как раньше,
`<проект>/logs` (antagor читает их там).
"""

from pathlib import Path

import platformdirs

from src.cli.project import ProjectContext


class TestLogsFolder:
    def test_without_project_logs_go_to_user_log_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        folder = ProjectContext().logs_folder

        assert folder == platformdirs.user_log_path("wp-bulk")
        assert not folder.is_relative_to(tmp_path)

    def test_project_keeps_its_logs_folder(self, tmp_path):
        assert ProjectContext(name="mw", root=tmp_path).logs_folder == Path(tmp_path) / "logs"
