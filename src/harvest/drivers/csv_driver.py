import csv
from io import StringIO
from harvest.exception import NotSupportCharCodeError, MaxImportCountExceededError

MAX_IMPORT_COUNT = 300
MAX_CSV_BYTES = 1024 * 1024
HEADERS = {
    "親": "parent",
    "種類": "type",
    "名前": "name",
    "parent": "parent",
    "type": "type",
    "name": "name",
}
TYPES = {"場所": "place", "撮影対象": "target", "place": "place", "target": "target"}


class CsvDriver:
    def __init__(self, filename=None):
        if filename:
            self.items = self.open_csv(filename)

    def parse(self, data):
        if len(data) > MAX_CSV_BYTES:
            raise ValueError("CSV exceeds the byte limit")
        for encoding in ("utf-8-sig", "cp932"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise NotSupportCharCodeError()
        reader = csv.DictReader(StringIO(text, newline=""), strict=True)
        fields = [HEADERS.get(name.strip()) for name in (reader.fieldnames or [])]
        if sorted(field or "" for field in fields) != ["name", "parent", "type"]:
            raise ValueError("CSV must have parent, type and name columns exactly once")
        rows: list[dict[str, str]] = []
        for row in reader:
            if len(rows) == MAX_IMPORT_COUNT:
                raise MaxImportCountExceededError()
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV row does not match its headers")
            item = {HEADERS[key.strip()]: value.strip() for key, value in row.items()}
            if not item["name"] or len(item["name"]) > 255 or len(item["parent"]) > 255:
                raise ValueError("CSV names must contain 1 to 255 characters")
            if item["type"] not in TYPES:
                raise ValueError("CSV type must be place or target")
            item["type"] = TYPES[item["type"]]
            rows.append(item)
        if not rows:
            raise ValueError("CSV contains no records")
        return rows

    def open_csv(self, filename):
        with open(filename, "rb") as source:
            return self.parse(source.read(MAX_CSV_BYTES + 1))
