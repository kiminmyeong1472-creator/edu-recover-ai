#!/usr/bin/env python3
"""Controlled local importer; only verified official citations and OliveYoung products.

No network requests. Existing products are updated by stable ID; formula changes are
reported for review. Never import example placeholders as genuine product data.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rug_full_rag import RuGFullRAG


def items(path: str) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, list) or len(payload) > 5000 or any(not isinstance(x, dict) for x in payload):
        raise ValueError('자료는 5000개 이하 JSON 객체 배열이어야 합니다.')
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description='RuG 공식 근거·제품·성분 동의어 승인 자료 가져오기')
    p.add_argument('--database', help='선택적 로컬 시험용 SQLite DB 파일')
    p.add_argument('--evidence', help='출처 확인된 공식 근거 JSON')
    p.add_argument('--products', help='실제 올리브영 판매 확인 제품 JSON')
    p.add_argument('--aliases', help='대한화장품협회/CosIng 검증 동의어 JSON')
    opts = p.parse_args()
    if not any([opts.evidence, opts.products, opts.aliases]):
        p.error('--evidence, --products, --aliases 중 하나는 필요합니다.')
    store = RuGFullRAG(opts.database)
    try:
        if opts.evidence:
            data = items(opts.evidence)
            for row in data:
                store.add_evidence(row)
            print('공식 근거 등록:', len(data), '건 ·', store.source_counts())
        if opts.aliases:
            data = items(opts.aliases)
            for row in data:
                store.add_alias(row['alias'], row['canonical'], row['source_id'], row['source_url'])
            print('공식 동의어 등록:', len(data), '건')
        if opts.products:
            data = items(opts.products)
            changes = [store.import_product(row) for row in data]
            print('올리브영 확인 제품 등록:', len(changes), '건')
            for row in changes:
                if row['formula_changed']:
                    print('성분 변경 감지·기존 분석 재검토 필요:', row['product_id'])
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print('자료 반입 실패:', str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
