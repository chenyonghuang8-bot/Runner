"""Offline comparison of annotated fields and validated recognition readings; no API."""
import argparse
import json
from pathlib import Path


def overlaps(a,b):
    return a[0] < b[0]+b[2] and b[0] < a[0]+a[2] and a[1] < b[1]+b[3] and b[1] < a[1]+a[3]


def compare(expected, readings):
    rows=[]
    for e in expected['fields']:
        found=[r for r in readings if r['field']==e['field']]
        errors=[]
        verified_evidence=False
        unverified_evidence=0
        if not found: errors.append('missing')
        for r in found:
            if r.get('value')!=e['value']:errors.append('value_mismatch')
            if r.get('unit')!=e['unit']:errors.append('unit_mismatch')
            if r.get('origin')!=e['origin']:errors.append('origin_mismatch')
            if r.get('issue'):errors.append('validation_issue')
            if not r.get('raw'):errors.append('missing_raw')
            if r.get('source_rect') and overlaps(r['source_rect'],e['source_rect']):verified_evidence=True
            else:
                unverified_evidence+=1
                errors.append('evidence_mismatch')
        if found and not verified_evidence:errors.append('evidence_mismatch')
        rows.append({'field':e['field'],'passed':not errors,'values_match':bool(found) and 'value_mismatch' not in errors,'errors':sorted(set(errors)), 'unverified_duplicate_evidence_count':unverified_evidence})
    return {'scope':'annotated_fields_only','count':len(rows),'passed':sum(r['passed'] for r in rows),
            'values_match_count':sum(r['values_match'] for r in rows),'complete':bool(rows) and all(r['passed'] for r in rows),'fields':rows,
            'unannotated_fields':sorted({r['field'] for r in readings}-{e['field'] for e in expected['fields']}),
            'note':'模块框交集只是证据位置初筛，不能代替人工核对字段标签；未标注字段不计准确率。'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expected',type=Path)
    parser.add_argument('result',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    expected=json.loads(args.expected.read_text())
    result=json.loads(args.result.read_text())
    report=compare(expected,result['readings'])
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f"已标注字段通过 {report['passed']}/{report['count']}；完整通过：{report['complete']}")
    return 0 if report['complete'] else 1

if __name__=='__main__':raise SystemExit(main())
