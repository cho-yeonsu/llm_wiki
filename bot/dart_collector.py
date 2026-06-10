#!/usr/bin/env python3
"""DART 공시 원문(XML)을 수집해서 sources/dart/에 저장한다."""

import os
import io
import time
import yaml
import zipfile
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path
from bs4 import BeautifulSoup

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_DOC_URL  = "https://opendart.fss.or.kr/api/document.json"
SOURCES_DIR   = Path("sources/dart")

ALL_TYPES = {
    "A": "정기공시",
    "B": "주요사항보고",
    "C": "발행공시",
    "D": "지분공시",
    "E": "기타공시",
    "F": "외부감사관련",
    "G": "펀드공시",
    "H": "자산유동화관련",
    "I": "거래소공시",
    "J": "공정위공시",
}


def fetch_list(api_key: str, corp_code: str, bgn_de: str, end_de: str) -> list[dict]:
    resp = httpx.get(
        DART_LIST_URL,
        params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_count": 40,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("list", []) if data.get("status") == "000" else []


def fetch_document_text(api_key: str, rcept_no: str, max_chars: int = 8000) -> str:
    """공시 원문 ZIP을 다운로드해서 XML 텍스트를 추출한다."""
    try:
        resp = httpx.get(
            DART_DOC_URL,
            params={"crtfc_key": api_key, "rcept_no": rcept_no},
            timeout=30,
        )
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            texts = []
            for name in sorted(zf.namelist()):
                if not name.endswith(".xml"):
                    continue
                raw = zf.read(name).decode("utf-8", errors="replace")
                soup = BeautifulSoup(raw, "lxml")
                texts.append(soup.get_text(separator="\n", strip=True))

        full_text = "\n\n".join(texts)
        if len(full_text) > max_chars:
            return full_text[:max_chars] + "\n\n...(이하 생략)"
        return full_text

    except Exception as e:
        return f"(원문 추출 실패: {e})"


def make_source_content(item: dict, company_name: str, doc_text: str) -> str:
    rcept_no  = item.get("rcept_no", "")
    title     = item.get("report_nm", "")
    date_raw  = item.get("rcept_dt", "")
    date_fmt  = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}" if len(date_raw) == 8 else date_raw
    type_name = ALL_TYPES.get(item.get("pblntf_ty", ""), "기타")
    url       = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

    return f"""# [DART] {company_name} - {title}

**수집일**: {datetime.now().strftime('%Y-%m-%d')}
**공시일**: {date_fmt}
**공시유형**: {type_name}
**원문**: {url}

## 원문 내용

{doc_text}
"""


def main():
    api_key = os.environ["DART_API_KEY"]

    codes_path = Path("schema/codes.yaml")
    if not codes_path.exists():
        print("schema/codes.yaml 없음. 종료.")
        return

    data = yaml.safe_load(codes_path.read_text(encoding="utf-8"))
    companies = [
        {"name": c["canonical"], "corp_code": c["dart_corp_code"]}
        for c in data.get("companies", [])
        if c.get("dart_collect") and c.get("dart_corp_code")
    ]
    target_types = set(data.get("disclosure_types", ["B", "I"]))

    if not companies:
        print("watchlist에 기업 없음. 종료.")
        return

    print(f"수집 공시유형: {', '.join(ALL_TYPES.get(t, t) for t in sorted(target_types))}\n")

    today     = datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)
    bgn_de    = yesterday.strftime("%Y%m%d")
    end_de    = today.strftime("%Y%m%d")

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    saved = 0
    for company in companies:
        name      = company["name"]
        corp_code = company["corp_code"]
        print(f"{name} ({corp_code}) 공시 수집 중...")

        try:
            items = fetch_list(api_key, corp_code, bgn_de, end_de)
            for item in items:
                if item.get("pblntf_ty") not in target_types:
                    continue

                rcept_no  = item.get("rcept_no", "")
                file_path = SOURCES_DIR / f"{bgn_de}_{rcept_no}.md"

                if file_path.exists():
                    continue

                print(f"  다운로드: {item.get('report_nm', '')} ({rcept_no})")
                doc_text = fetch_document_text(api_key, rcept_no)
                time.sleep(0.5)  # API 속도 제한 대응

                file_path.write_text(
                    make_source_content(item, name, doc_text),
                    encoding="utf-8",
                )
                saved += 1

        except Exception as e:
            print(f"  ❌ {name} 실패: {e}")

    print(f"\n총 {saved}개 공시 저장")


if __name__ == "__main__":
    main()
