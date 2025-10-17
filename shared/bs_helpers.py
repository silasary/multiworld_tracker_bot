from bs4 import Tag


def try_int(text: Tag | str) -> str | int:
    if isinstance(text, Tag):
        if text.string:
            text = text.string
        else:
            text = text.get_text()
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        return text


def process_table(table: Tag) -> list[dict]:
    headers = [i.string for i in table.find_all("th")]
    rows = [[try_int(i) for i in r.find_all("td")] for r in table.find_all("tr")[1:]]
    return [dict(zip(headers, r)) for r in rows]
