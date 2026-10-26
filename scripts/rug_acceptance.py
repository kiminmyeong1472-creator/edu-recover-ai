"""Run the recorded ten official-source and ten absent-fact checks on a real index."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.rug_full_rag import RuGFullRAG


def evaluate(store,questions):
    known=[]
    for row in questions['known']:
        found=store.retrieve(row['question'],limit=5)
        answer=store.grounded_answer(row['question'])
        matching=[r for r in found if r['source_id']==row['expected_source'] and row['expected_phrase'] in r['passage']]
        valid=bool(matching and answer['state']=='EVIDENCE_FOUND' and
            all(store.verify_sentence(s['text'],s['citation']) for s in answer['sentences']))
        known.append({**row,'passed':valid,'top_five_chunk_ids':[r['chunk_id'] for r in found],
            'matching_document_sha256':[r['text_sha256'] for r in matching],
            'state':answer['state'],'search_mode':answer['search_mode']})
    absent=[]
    for question in questions['absent']:
        answer=store.grounded_answer(question)
        absent.append({'question':question,'passed':answer['state']=='NO_EVIDENCE' and not answer['citations'],
            'state':answer['state']})
    return {'known':known,'absent':absent,'known_passed':sum(r['passed'] for r in known),
        'absent_passed':sum(r['passed'] for r in absent),'coverage':store.coverage(),
        'all_passed':all(r['passed'] for r in known+absent)}


def main():
    parser=argparse.ArgumentParser(description='실제 공식자료 색인의 RuG 동작 검사')
    parser.add_argument('--database')
    parser.add_argument('--questions',type=Path,default=Path(__file__).resolve().parents[1]/'tests/fixtures/rug_official_questions.json')
    args=parser.parse_args()
    result=evaluate(RuGFullRAG(args.database),json.loads(args.questions.read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['all_passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
