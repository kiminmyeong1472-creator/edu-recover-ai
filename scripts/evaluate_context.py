"""Evaluate independently annotated saved outputs; no model call or training."""
from pathlib import Path
import argparse
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.context_evaluation import Case, Observation, summarize


def load_lines(path):
    return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', required=True)
    p.add_argument('--observations', required=True)
    p.add_argument('--stages', nargs='+', required=True)
    p.add_argument('--evidence-kind', choices=['synthetic','live_reviewed'], default='synthetic')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    report = summarize([Case(**r) for r in load_lines(a.cases)],
                       [Observation(**r) for r in load_lines(a.observations)],
                       expected_stages=a.stages, evidence_kind=a.evidence_kind)
    # This label is not authentication. A trusted service must verify live records.
    with Path(a.output).open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
    print(json.dumps({'stages':{k:v['status'] for k,v in report['stages'].items()},
                      'model_called':False,'weights_updated':False,'bank_written':False}))


if __name__ == '__main__':
    main()
