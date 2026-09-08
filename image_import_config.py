"""画像取込の端末ローカル設定（共有バックアップ先など）。"""

import json
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("image_import_config.json")


def load_backup_directory(default: Path) -> Path:
    if not CONFIG_PATH.is_file():
        return Path(default).resolve()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        value = data.get("backup_directory")
        return Path(value).resolve() if value else Path(default).resolve()
    except (OSError, ValueError, TypeError):
        return Path(default).resolve()


def save_backup_directory(directory: Path) -> Path:
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"backup_directory": str(directory)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(CONFIG_PATH)
    return directory


def choose_backup_directory(current: Path) -> Path | None:
    """Windowsのフォルダー選択ダイアログを表示する。キャンセル時はNone。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("このPCではフォルダー選択画面を開けません") from exc
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title="画像取込バックアップの保存先を選択",
            initialdir=str(current) if Path(current).is_dir() else str(Path.home()),
            mustexist=False,
            parent=root,
        )
    finally:
        root.destroy()
    return Path(selected) if selected else None
