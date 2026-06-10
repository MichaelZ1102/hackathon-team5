import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MOCK_DATA_DIR = PROJECT_ROOT / "mock_data"


def load_json(file_name_or_path):
    path = Path(file_name_or_path)
    if not path.is_absolute():
        path = MOCK_DATA_DIR / path

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(file_name_or_path, data):
    path = Path(file_name_or_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")

