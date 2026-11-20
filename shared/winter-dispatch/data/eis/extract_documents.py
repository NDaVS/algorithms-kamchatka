"""Extract work rates and object lists from the archived EIS documents."""

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile


DIRECTORY = Path(__file__).resolve().parent
SOURCE = DIRECTORY.parents[1] / "docs" / "sources" / "eis"
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_positions(path):
    root = ET.parse(path).getroot()
    positions = []
    for item in root.findall(".//{*}productInfo"):
        name = item.findtext("{*}name", "")
        name = " ".join(name.split())
        if name.startswith("Зимнее содержание"):
            season = "winter"
        elif name.startswith("Летнее содержание"):
            season = "summer"
        elif name.startswith("Ремонтные работы"):
            season = "repair"
        elif "снега" in name.casefold():
            season = "snow_handling"
        else:
            season = None
        price = item.findtext("{*}price")
        positions.append({
            "item_number": int(item.findtext("{*}indexNum")),
            "name": name,
            "operation_group": season,
            "okpd2": item.findtext("{*}OKPD2Info/{*}OKPDCode"),
            "unit": item.findtext("{*}OKEIInfo/{*}name"),
            "unit_price_kopecks": int(Decimal(price) * 100) if price else None,
            "volume_code": item.findtext("{*}indicationSubjects"),
        })
    return sorted(positions, key=lambda item: item["item_number"])


def text_of(element):
    return " ".join("".join(node.text or "" for node in element.iter(WORD + "t")).split())


def read_objects(path):
    with ZipFile(path) as document:
        root = ET.fromstring(document.read("word/document.xml"))
    objects = []
    heading = ""
    section_pattern = r"(?:2\.4|4\.2)\.\d+"
    for element in root.find(WORD + "body"):
        if element.tag == WORD + "p":
            entry = text_of(element)
            if entry:
                heading = entry
        if element.tag != WORD + "tbl" or not re.match(section_pattern + r"\.", heading):
            continue
        detail = None
        for row in element.findall(WORD + "tr"):
            cells = [text_of(cell) for cell in row.findall(WORD + "tc")]
            if len(cells) == 1:
                if re.match(section_pattern + r"\.", cells[0]):
                    heading = cells[0]
                    detail = None
                else:
                    detail = cells[0]
                continue
            if not cells or not cells[1] or cells[0].startswith("№") or all(c.isdigit() for c in cells):
                continue
            if cells[1].startswith("Общая площадь"):
                continue
            objects.append({
                "section": re.match(section_pattern, heading).group(),
                "section_title": heading,
                "subheading": detail,
                "row_number": cells[0] or None,
                "columns": cells[1:],
            })
    return objects


def main():
    registry = json.loads((DIRECTORY / "contracts.json").read_text())
    documents = []
    for contract in registry["contracts"]:
        filename = contract["contract_id"] + ".xml"
        path = SOURCE / filename
        if not path.is_file():
            continue
        spec_name = contract["notice_id"] + "-spec.docx"
        positions = read_positions(path)
        objects = read_objects(SOURCE / spec_name)
        documents.append({
            "contract_id": contract["contract_id"],
            "notice_id": contract["notice_id"],
            "supplier": contract["supplier"],
            "scope": "Расценки из XML с учётом соглашений. Объекты из ТЗ извещения.",
            "price_source": filename,
            "object_source": spec_name,
            "positions": positions,
            "objects": objects,
        })
        print(contract["contract_id"], len(positions), dict(Counter(x["section"] for x in objects)))
    (DIRECTORY / "document_extracts.json").write_text(
        json.dumps(documents, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
