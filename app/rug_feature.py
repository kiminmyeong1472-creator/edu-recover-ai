"""Authenticated RuG ingredient-list input with encrypted profiles and history."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import get_resource, list_resources, put_resource, resource_transaction
from app.rug_engine import norm, now_iso, oliveyoung_url
from app.rug_formula import changes, compare_formula, parse_formula, product_key
from app.rug_full_rag import RuGFullRAG
from app.rug_ocr import CONTENT_TYPES, MAX_IMAGE_BYTES, OcrNotInstalled, PhotoUnreadable, read_label
from app.services.secret_vault import state_root

PROFILE_KIND = 'rug_allergens_v1'
HISTORY_KIND = 'rug_analysis_v2'
SECURE = {'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY'}


def _private_response(response: Response):
    response.headers.update(SECURE)


router = APIRouter(tags=['life-rug'],dependencies=[Depends(_private_response)])


def _storage_ready():
    mount = os.getenv('RAILWAY_VOLUME_MOUNT_PATH','').strip()
    if mount and not state_root().resolve().is_relative_to(Path(mount).resolve()):
        raise HTTPException(503,'rug_storage_outside_persistent_volume')


def _store():
    _storage_ready()
    return RuGFullRAG()


def _profile_id(subject):
    return 'rug_profile_'+hashlib.sha256(subject.encode()).hexdigest()[:28]


def _allergens(subject):
    _storage_ready()
    item = get_resource(domain='life',subject_id=subject,resource_id=_profile_id(subject))
    return item['payload'].get('allergens',[]) if item and item['kind']==PROFILE_KIND else []


def _check_post(request):
    parsed = urlsplit(request.headers.get('origin',''))
    if (parsed.scheme not in {'http','https'} or not parsed.netloc or parsed.netloc.lower()!=request.headers.get('host','').lower()
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise HTTPException(403,'same_origin_required')
    if request.headers.get('x-rug-consent')!='1':
        raise HTTPException(403,'explicit_consent_required')


class AllergenUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allergens: list[str] = Field(max_length=50)

    @field_validator('allergens')
    @classmethod
    def valid_allergens(cls,values):
        if any(not v.strip() or len(v)>120 or any(ord(c)<32 for c in v) for v in values):
            raise ValueError('invalid_allergen_name')
        # Preserve each original registered spelling and order, including aliases.
        return list(dict.fromkeys(values))


class FormulaInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    raw_text: str = Field(max_length=100_000)
    corrected_ingredients: list[str] | None = Field(default=None,max_length=400)


class AnalyzeRequest(FormulaInput):
    product_name: str = Field(min_length=1,max_length=200)
    oliveyoung_url: str = Field(default='',max_length=2048)
    complete: bool = False
    expected_count: int | None = Field(default=None,ge=0,le=400)
    previous_review_id: str | None = Field(default=None,max_length=100)
    reviewed_names: list[str] | None = Field(default=None,max_length=400)
    reading_source: str = Field(default='typed',pattern='^(typed|photo_ocr)$')
    explicit_consent: bool

    @field_validator('product_name')
    @classmethod
    def valid_name(cls,value):
        if not value.strip() or any(ord(c)<32 for c in value):
            raise ValueError('invalid_product_name')
        return value.strip()

    @field_validator('oliveyoung_url')
    @classmethod
    def valid_url(cls,value):
        if value and not oliveyoung_url(value):
            raise ValueError('oliveyoung_product_link_required')
        return value

    @field_validator('explicit_consent')
    @classmethod
    def consent(cls,value):
        if value is not True:
            raise ValueError('explicit_consent_required')
        return value


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    q: str = Field(min_length=2,max_length=240)


def _run_analysis(subject,payload):
    store = _store()
    # The confirmation time is stamped by the server; a client cannot backdate it.
    review = ({'confirmed_at':now_iso(),'reviewed_names':payload.reviewed_names}
              if payload.reviewed_names else None)
    outcome = compare_formula(store,product_name=payload.product_name,raw_text=payload.raw_text,
        allergens=_allergens(subject),complete=payload.complete,expected_count=payload.expected_count,
        corrected=payload.corrected_ingredients,oliveyoung_link=payload.oliveyoung_url,
        reading_source=payload.reading_source,user_review=review)
    with resource_transaction('life'):
        previous = None
        if payload.previous_review_id:
            previous = get_resource(domain='life',subject_id=subject,resource_id=payload.previous_review_id)
            if (not previous or previous['kind']!=HISTORY_KIND or
                previous['payload']['analysis']['product_key']!=outcome['product_key']):
                raise HTTPException(404,'previous_review_not_found')
        else:
            histories = list_resources(domain='life',subject_id=subject,kind=HISTORY_KIND)
            previous = next((r for r in histories if r['payload']['analysis']['product_key']==outcome['product_key']),None)
        outcome['review_id'] = 'rug_review_'+uuid.uuid4().hex
        outcome['checked_at'] = now_iso()
        outcome['previous_review_id'] = previous['resource_id'] if previous else None
        outcome['changes'] = changes(previous['payload']['analysis'],outcome) if previous else None
        # No customer input is written into the public retrieval database.
        put_resource(domain='life',subject_id=subject,kind=HISTORY_KIND,resource_id=outcome['review_id'],payload={'analysis':outcome})
    return outcome


@router.get('/life/rug',response_class=HTMLResponse)
def rug_page(request: Request):
    try:
        require_life_user(request)
    except HTTPException as exc:
        if exc.status_code==401:
            return RedirectResponse('/life/auth/login',status_code=303)
        raise
    page = (Path(__file__).parent/'static'/'rug.html').read_text(encoding='utf8')
    return HTMLResponse(page,headers={**SECURE,'Content-Security-Policy':
        "default-src 'self'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})


@router.get('/life/rug/app.js')
def rug_script(request: Request):
    require_life_user(request)
    return Response((Path(__file__).parent/'static'/'rug_ui.js').read_text(),media_type='text/javascript',headers=SECURE)


@router.get('/life/api/rug/allergens')
def get_allergens(request: Request):
    subject = require_life_user(request)
    return {'allergens':_allergens(subject)}


@router.post('/life/api/rug/allergens')
def save_allergens(request: Request,payload: AllergenUpdate):
    subject = require_life_user(request)
    _check_post(request)
    _storage_ready()
    put_resource(domain='life',subject_id=subject,kind=PROFILE_KIND,resource_id=_profile_id(subject),payload={'allergens':payload.allergens})
    return {'allergens':payload.allergens}


@router.post('/life/api/rug/formula/preview')
def preview(request: Request,payload: FormulaInput):
    require_life_user(request)
    _check_post(request)
    try:
        return parse_formula(payload.raw_text,corrected=payload.corrected_ingredients)
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,str(exc)) from None


@router.post('/life/api/rug/analyze')
def analyze_formula(request: Request,payload: AnalyzeRequest):
    subject = require_life_user(request)
    _check_post(request)
    try:
        return _run_analysis(subject,payload)
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,str(exc)) from None


@router.post('/life/api/rug/photo')
async def analyze_photo(request: Request,photo: UploadFile = File(...),
                        product_name: str = Form(...),oliveyoung_url: str = Form(''),
                        explicit_consent: str = Form(...)):
    """One tap: read the label photo, then run the same rule comparison at once.

    The photo is never stored and never decides the verdict. Because a photo can
    cut off or blur part of a label, the input is never marked complete, so a
    partial read ends in HOLD with the confirmed-count notice instead of a
    false 'not found'.
    """
    subject = require_life_user(request)
    _check_post(request)
    if explicit_consent != 'true':
        raise HTTPException(403,'explicit_consent_required')
    if photo.content_type not in CONTENT_TYPES:
        raise HTTPException(415,'unsupported_image_type')
    image = await photo.read(MAX_IMAGE_BYTES + 1)
    if len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(413,'image_size_invalid')
    try:
        label = read_label(image,photo.content_type)
    except OcrNotInstalled:
        raise HTTPException(503,'ocr_not_installed') from None
    except PhotoUnreadable as exc:
        raise HTTPException(422,str(exc)) from None
    finally:
        del image
    try:
        payload = AnalyzeRequest(raw_text='\n'.join(label['ingredients']),product_name=product_name,
            oliveyoung_url=oliveyoung_url,complete=False,reading_source='photo_ocr',explicit_consent=True)
        outcome = _run_analysis(subject,payload)
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,str(exc)) from None
    outcome['input_source'] = 'photo'
    outcome['read_ingredients'] = label['ingredients']
    outcome['ocr_text'] = label['text'][:20000]
    outcome['ocr_language'] = label['language']
    outcome['korean_ocr_available'] = label['korean_available']
    return outcome


@router.get('/life/api/rug/rag/status')
def rag_status(request: Request):
    require_life_user(request)
    return _store().coverage()


@router.post('/life/api/rug/rag/search')
def rag_search(request: Request,payload: SearchInput):
    require_life_user(request)
    _check_post(request)
    return _store().grounded_answer(payload.q)


@router.get('/life/api/rug/history')
def history(request: Request):
    subject = require_life_user(request)
    _storage_ready()
    return {'history':[{'review_id':r['resource_id'],**r['payload']['analysis']}
                       for r in list_resources(domain='life',subject_id=subject,kind=HISTORY_KIND)[:100]]}
