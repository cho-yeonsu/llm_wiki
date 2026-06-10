"""엔티티 표준명 해석 및 [[위키링크]] 정규화."""

import re
import yaml
from pathlib import Path


def load_companies(codes_path: str = "schema/codes.yaml") -> list[dict]:
    data = yaml.safe_load(Path(codes_path).read_text(encoding="utf-8"))
    return data.get("companies", [])


def build_alias_map(companies: list[dict]) -> dict[str, str]:
    """alias → canonical 역매핑. 긴 alias 우선 적용."""
    alias_map = {}
    for company in companies:
        canonical = company["canonical"]
        for alias in company.get("aliases", []):
            alias_map[alias] = canonical
    return alias_map


def resolve_wikilinks(text: str, alias_map: dict[str, str]) -> str:
    """[[alias]] → [[canonical]] 교체. 텍스트 내 alias는 건드리지 않는다."""
    if not alias_map:
        return text

    def replace(m: re.Match) -> str:
        inner = m.group(1).strip()
        return f"[[{alias_map.get(inner, inner)}]]"

    return re.sub(r"\[\[([^\]]+)\]\]", replace, text)


def build_entity_context(companies: list[dict]) -> str:
    """Claude 프롬프트에 삽입할 표준명 목록."""
    lines = []
    for c in companies:
        codes = []
        if c.get("kr_code"):
            codes.append(f"KR {c['kr_code']}")
        if c.get("us_ticker"):
            codes.append(f"US {c['us_ticker']}")
        code_str = f" ({', '.join(codes)})" if codes else ""
        aliases = c.get("aliases", [])
        alias_str = f" | 별칭: {', '.join(aliases)}" if aliases else ""
        lines.append(f"- {c['canonical']}{code_str}{alias_str}")
    return "\n".join(lines)
