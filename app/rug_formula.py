"""Parse already analyzed ingredient lists and compare registered names by rules."""
from __future__ import annotations

import csv
import io
import re
import unicodedata

from app.rug_engine import INCOMPLETE_NOTICE, digest, norm, oliveyoung_url

REVIEW_NOTICE = ('읽어 들인 이름 가운데 표준 성분명으로 확인되지 않은 것이 {count}개 있습니다. '
                 '글자가 잘못 읽혔을 수 있어 최종 판정을 내리지 않았습니다. '
                 '해당 이름을 확인해 고치시거나 그대로 두겠다고 확인해 주셔야 판정을 진행합니다.')
GROUP_NAMES = {'향료','퍼퓸','fragrance','parfum','aroma','향','미확인','unknown','기타','etc','...','…'}
UNKNOWN_NAMES = {'미확인','unknown','기타','etc','...','…'}


def parse_formula(raw: str, *, corrected: list[str] | None = None):
    if len(raw) > 100_000 or '\x00' in raw:
        raise ValueError('ingredient_input_invalid')
    if corrected is None:
        # Numeric INCI locants such as 1,2-Hexanediol belong to one ingredient.
        token = '\uE000'
        if token in raw:
            raise ValueError('ingredient_input_invalid')
        protected = re.sub(r'(?<=\d),(?=\d)',token,raw.lstrip('\ufeff'))
        values = [v.replace(token,',').strip() for row in csv.reader(io.StringIO(protected)) for v in row if v.strip()]
        if values and norm(values[0]) in {'ingredient','ingredients','성분','전성분'}:
            values = values[1:]
    else:
        values = [x.strip() for x in corrected]
    if len(values)>400 or any(not v or len(v)>180 or any(ord(c)<32 for c in v) for v in values):
        raise ValueError('ingredient_list_invalid')
    rows = [{'original':v,'normalized':norm(v)} for v in values]
    known = [r for r in rows if r['normalized'] not in UNKNOWN_NAMES]
    return {'raw_text':raw,'ingredients':values,'normalization':rows,
            'confirmed_count':len({r['normalized'] for r in known}),
            'has_group':any(r['normalized'] in GROUP_NAMES or
                re.search(r'향료|fragrance|parfum|aroma',r['normalized']) for r in rows)}


def product_key(name: str, url: str = '') -> str:
    return digest([unicodedata.normalize('NFKC',name).casefold().strip(),url])


def compare_formula(store, *, product_name, raw_text, allergens, complete=False,
                    expected_count=None, corrected=None, oliveyoung_link='',
                    reading_source='typed', user_review=None):
    """Compare an ingredient list against the person's registered allergens.

    A name that the verified ingredient dictionary cannot resolve is never
    silently dropped: it is returned as an unresolved name and, until the person
    who supplied the list has reviewed those names, no final verdict is issued.
    Automatic character recognition can mangle an allergen (for example 리모넨 into
    F|GUl), and a mangled name would otherwise read as 'not found'.
    """
    parsed = parse_formula(raw_text,corrected=corrected)
    values = parsed['ingredients']
    incomplete = (not complete or not values or parsed['has_group'] or
                  (expected_count is not None and parsed['confirmed_count']<expected_count))
    issues = []
    if incomplete:
        issues.append('일부 성분 미확인')
    verified_products = [p for p in store.products() if norm(p['name'])==norm(product_name)
                        and p.get('oliveyoung_verified') is True
                        and oliveyoung_url(p.get('oliveyoung_url',''))]
    if oliveyoung_link:
        verified_products = [p for p in verified_products if p['oliveyoung_url']==oliveyoung_link]
    if len(verified_products)!=1:
        issues.append('올리브영 판매 제품 확인 자료 부족 또는 제품명 중복')
    else:
        from app.rug_engine import _fresh_date
        catalog = verified_products[0]
        if not _fresh_date(catalog.get('ingredient_checked_at')):
            issues.append('제품 전성분 확인일 경과 또는 미확인')
        if (catalog.get('ingredients_complete') and complete and
                {norm(v) for v in catalog['ingredients']}!={norm(v) for v in values}):
            issues.append('등록된 제품 자료와 입력한 전성분이 다름')
        if any({norm(v) for v in other}!={norm(v) for v in catalog['ingredients']}
               for other in catalog.get('alternate_official_ingredient_lists',[])):
            issues.append('공식 출처 간 전성분 불일치')
    if not allergens:
        issues.append('등록된 개인 알레르겐 없음')
    normalized = set()
    ambiguous = False
    unresolved = []
    for value in values:
        canonical, conflict = store.canonical(value)
        ambiguous |= conflict
        if canonical and store.known(value) and norm(value) not in UNKNOWN_NAMES:
            normalized.add(canonical)
        elif norm(value) not in UNKNOWN_NAMES and value not in unresolved:
            unresolved.append(value)
    if not store.dictionary_loaded():
        # Without the dictionary every name would look unreadable; say so instead.
        unresolved = []
        issues.append('표준 성분명 사전 미반입')
    matches = []
    for original in allergens:
        canonical, conflict = store.canonical(original)
        ambiguous |= conflict
        if canonical in normalized or norm(original) in {norm(v) for v in values}:
            matches.append(original)
    if ambiguous:
        issues.append('성분 동의어 충돌')
    reviewed = bool(user_review and user_review.get('confirmed_at')
                    and set(user_review.get('reviewed_names') or []) >= set(unresolved))
    needs_review = bool(unresolved) and not reviewed
    if unresolved:
        issues.append('표준 성분명으로 확인되지 않은 이름 %d개' % len(unresolved))
    # These two outputs have separate authority. Retrieval never rewrites this comparison.
    presence = 'MATCH' if matches else ('NO_MATCH' if values and allergens and not ambiguous else 'HOLD')
    if needs_review:
        # The person who supplied the list decides what an unreadable name is.
        # Until then the comparison must not stand as a verdict.
        presence = 'HOLD'
    state = 'NEEDS_USER_REVIEW' if needs_review else ('HOLD' if issues else presence)
    identity_url = verified_products[0]['oliveyoung_url'] if len(verified_products)==1 else oliveyoung_link
    return {'status':state,'presence':presence,'product_name':product_name,'product_key':product_key(product_name,identity_url),
            'raw_text':raw_text,**parsed,'formula_digest':digest(values),
            'registered_allergens':list(allergens),'matches':matches,'issues':issues,
            'incomplete_ingredients':incomplete,
            'unresolved_ingredients':unresolved,'requires_user_review':needs_review,
            'reading_source':reading_source,'user_review':user_review if reviewed else None,
            'review_notice':(REVIEW_NOTICE.format(count=len(unresolved)) if needs_review else ''),
            'incomplete_notice':INCOMPLETE_NOTICE.format(count=parsed['confirmed_count']) if incomplete else '',
            'message':REVIEW_NOTICE.format(count=len(unresolved)) if needs_review else
                '등록하신 성분과 일치한 성분이 있습니다.' if matches else
                ('확인된 범위에서는 등록하신 성분이 발견되지 않았습니다' if values and allergens else '판정을 보류했습니다.'),
            'sale_verification':'verified_catalog' if len(verified_products)==1 else 'unverified',
            'scope':'customer_supplied_ingredient_list'}


def changes(previous, current):
    old = {norm(v):v for v in previous.get('ingredients',[])}
    new = {norm(v):v for v in current.get('ingredients',[])}
    return {'ingredients_added':[v for k,v in new.items() if k not in old],
            'ingredients_removed':[v for k,v in old.items() if k not in new],
            'matches_added':[v for v in current['matches'] if v not in previous.get('matches',[])],
            'matches_removed':[v for v in previous.get('matches',[]) if v not in current['matches']],
            'previous_count':previous.get('confirmed_count',0),'current_count':current['confirmed_count'],
            'previous_status':previous.get('status'),'current_status':current['status'],
            'formula_changed':previous.get('formula_digest')!=current['formula_digest']}
