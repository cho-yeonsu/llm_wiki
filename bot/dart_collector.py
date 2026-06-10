#!/usr/bin/env python3
"""DART 공시를 수집해서 sources/dart/에 저장한다."""

import os
import json
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
SOURCES_DIR = Path("sources/dart")

DISCLOSURE_TYPES = {
    "A": "정기공시",
    "B": "주요사항보고",
    "D": "지분공시",
    "I": "거래소공시",
}


def fetch_disclosures(api_key: str, corp_code: str, bgn_de: str, end_de: str) -> list[dict]:
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
    if data.get("status") != "000":
        return []
    return data.get("list", [])


def make_source_content(item: dict, company_name: str) -> str:
    rcept_no = item.get("rcept_no", "")
    title = item.get("report_nm", "")
    date_raw = item.get("rcept_dt", "")
    date_fmt = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}" if len(date_raw) == 8 else date_raw
    pblntf_nm = DISCLOSURE_TYPES.get(item.get("pblntf_ty", ""), item.get("pblntf_ty", "기타"))
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

    return f"""# [DART] {company_name} - {title}

**수집일**: {datetime.now().strftime('%Y-%m-%d')}
**공시일**: {date_fmt}
**공시유형**: {pblntf_nm}
**원문**: {url}

- 회사명: {company_name}
- 공시제목: {title}
- 접수번호: {rcept_no}

[DART 원문 보기]({url})
"""


def main():
    api_key = os.environ["DART_API_KEY"]

    watchlist_path = Path("schema/dart_watchlist.json")
    if not watchlist_path.exists():
        print("schema/dart_watchlist.json 없음. 종료.")
        return

    watchlist = json.loads(watchlist_path.read_text(encoding="utf-8"))
    companies = watchlist.get("companies", [])
    if not companies:
        print("watchlist에 기업 없음. 종료.")
        return

    today = datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)
    bgn_de = yesterday.strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    saved = 0
    for company in companies:
        name = company["name"]
        corp_code = company["corp_code"]
        print(f"{name} ({corp_code}) 공시 수집 중...")

        try:
            items = fetch_disclosures(api_key, corp_code, bgn_de, end_de)
            for item in items:
                if item.get("pblntf_ty") not in DISCLOSURE_TYPES:
                    continue

                rcept_no = item.get("rcept_no", "")
                file_path = SOURCES_DIR / f"{bgn_de}_{rcept_no}.md"

                if file_path.exists():
                    continue

                file_path.write_text(make_source_content(item, name), encoding="utf-8")
                print(f"  저장: {file_path.name} ({item.get('report_nm', '')})")
                saved += 1

        except Exception as e:
            print(f"  ❌ {name} 실패: {e}")

    print(f"\n총 {saved}개 공시 저장")


if __name__ == "__main__":
    main()
