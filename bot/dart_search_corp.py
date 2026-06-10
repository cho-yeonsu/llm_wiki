#!/usr/bin/env python3
"""DART 전체 기업 목록을 다운로드하고 회사명으로 corp_code를 검색한다.

사용법:
  python bot/dart_search_corp.py 삼성전기
  python bot/dart_search_corp.py SK

환경변수:
  DART_API_KEY: DART Open API 키 (opendart.fss.or.kr에서 무료 발급)
"""

import io
import os
import sys
import zipfile
import xml.etree.ElementTree as ET
import httpx

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"


def download_corp_list(api_key: str) -> list[dict]:
    """DART에서 전체 기업 코드 목록을 다운로드한다."""
    print("전체 기업 목록 다운로드 중...")
    resp = httpx.get(CORP_CODE_URL, params={"crtfc_key": api_key}, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_data = zf.read("CORPCODE.xml")

    root = ET.fromstring(xml_data)
    companies = []
    for item in root.findall("list"):
        corp_code  = (item.findtext("corp_code") or "").strip()
        corp_name  = (item.findtext("corp_name") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        modify_date = (item.findtext("modify_date") or "").strip()
        if corp_code and corp_name:
            companies.append({
                "corp_code":   corp_code,
                "corp_name":   corp_name,
                "stock_code":  stock_code,
                "modify_date": modify_date,
            })

    print(f"총 {len(companies):,}개 기업 로드 완료\n")
    return companies


def search(companies: list[dict], keyword: str) -> list[dict]:
    return [c for c in companies if keyword in c["corp_name"]]


def main():
    keyword = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else ""
    api_key = os.environ.get("DART_API_KEY", "")

    if not api_key:
        print("오류: DART_API_KEY 환경변수를 설정해주세요.")
        print("  export DART_API_KEY=your_key_here")
        sys.exit(1)

    companies = download_corp_list(api_key)

    if not keyword:
        print("검색어를 입력하세요.")
        print("  python bot/dart_search_corp.py 삼성")
        sys.exit(1)

    results = search(companies, keyword)

    if not results:
        print(f'"{keyword}" 검색 결과 없음')
        return

    print(f'"{keyword}" 검색 결과 {len(results)}건:')
    print(f"{'corp_code':<12} {'stock_code':<12} {'회사명'}")
    print("-" * 50)
    for c in results[:30]:
        stock = c["stock_code"] or "-"
        print(f"{c['corp_code']:<12} {stock:<12} {c['corp_name']}")

    if len(results) > 30:
        print(f"... 외 {len(results) - 30}건 (검색어를 더 구체적으로 입력하세요)")

    print("\n📋 dart_watchlist.json 추가 형식:")
    for c in results[:5]:
        print(f'  {{"name": "{c["corp_name"]}", "corp_code": "{c["corp_code"]}"}},')


if __name__ == "__main__":
    main()
