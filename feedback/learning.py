"""CPU-only supervised training and chronological evaluation. JSON logistic artifacts."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import resource
import time

from feedback.common import canonical, digest
from feedback.settings import load

VERSION = 'features-v1-384-64'
NEGATIVE_UTILITY = {'off_topic','not_applicable','shallow_or_promotional'}
TARGETS = ('utility','off_topic','not_applicable','shallow_or_promotional','not_new','already_covered')


def label(reason, target):
    # Conditional reason-vs-useful contrasts, not labels for latent quality dimensions.
    if target not in TARGETS:
        raise ValueError('Invalid target')
    if target == 'utility':
        return 1 if reason == 'useful' else 0 if reason in NEGATIVE_UTILITY else None
    return 1 if reason == target else 0 if reason == 'useful' else None


def vector(assessment):
    emb = assessment.get('embedding')
    if not isinstance(emb,list) or len(emb) != 384:
        raise ValueError('Missing 384-dimensional MiniLM embedding')
    values = [float(v) for v in emb]
    values += [float(assessment.get(k,0))/5 for k in ('technology','importance','business')]
    values += [float(assessment.get('watch_priority',0))/2]
    hashed = [0.] * 64
    fields = {'category':assessment.get('category','unknown')}
    fields.update({k:v.get('value','unknown') for k,v in assessment.get('editorial_features',{}).items()})
    for key,value in fields.items():
        i = int(digest([key,value])[:8],16) % 64
        hashed[i] += 1
    values += hashed
    if any(not (-1e10 < v < 1e10) for v in values):
        raise ValueError('Nonfinite or excessive features')
    return values


def effective(rows, at):
    """Only annotations actually imported by at; deterministic correction ordering."""
    result = {}
    for row in rows:
        if row['available_at'] >= at or row['observed_at'] >= at:
            continue
        old = result.get(row['presentation_id'])
        if old is None or (row['submitted_at'],row['return_id']) > (old['submitted_at'],old['return_id']):
            result[row['presentation_id']] = row
    return list(result.values())


def dedup_events(rows):
    by_event = {}
    for row in sorted(rows,key=lambda r:(r['observed_at'],r['presentation_id'])):
        by_event[row['event_id']] = row
    return list(by_event.values())


def eligible(rows, target):
    kept, skipped = [], Counter()
    for row in rows:
        y = label(row['reason'],target)
        if y is None:
            skipped['unknown_target'] += 1
            continue
        try:
            vector(row['assessment'])
        except (ValueError,TypeError):
            skipped['missing_features'] += 1
            continue
        kept.append(dict(row,y=y))
    return kept, dict(skipped)


def enough(rows, settings):
    counts = Counter(r['y'] for r in rows)
    return len(rows) >= settings.get('min_training_events',100) and all(
        counts.get(i,0) >= settings.get('min_class_events',10) for i in (0,1))


def fit(rows, backend='logistic'):
    import numpy as np
    x = np.array([vector(r['assessment']) for r in rows]); y = np.array([r['y'] for r in rows])
    if backend == 'logistic':
        from sklearn.linear_model import LogisticRegression
        model = LogisticRegression(C=1.,max_iter=1000,solver='liblinear',random_state=0)
        model.fit(x,y)
        return model, dict(version=VERSION,backend=backend,coefficients=model.coef_[0].tolist(),intercept=float(model.intercept_[0]))
    if backend == 'catboost':
        from catboost import CatBoostClassifier
        model = CatBoostClassifier(iterations=100,depth=3,learning_rate=.05,thread_count=2,
                                   random_seed=0,verbose=False,allow_writing_files=False)
        model.fit(x,y)
        return model, dict(version=VERSION,backend=backend)
    raise ValueError('Unknown backend')


def predict_json(artifact, assessment):
    import math
    if artifact.get('version') != VERSION or artifact.get('backend') != 'logistic':
        raise ValueError('Unsupported model artifact')
    x = vector(assessment); weights = artifact['coefficients']
    if not isinstance(weights,list) or any(type(v) not in (int,float) or not math.isfinite(v) for v in weights+[artifact['intercept']]):
        raise ValueError('Nonfinite model parameters')
    if len(weights) != len(x):
        raise ValueError('Invalid coefficients')
    z = artifact['intercept'] + sum(a*b for a,b in zip(x,weights))
    return 1 / (1 + math.exp(-max(-700,min(700,z))))


def neighbors(train, test, target):
    import numpy as np
    if not train:
        return [.5] * len(test)
    a = np.array([vector(r['assessment'])[:384] for r in train]); a /= np.maximum(np.linalg.norm(a,axis=1,keepdims=True),1e-12)
    out = []
    for row in test:
        v = np.array(vector(row['assessment'])[:384]); v /= max(float(np.linalg.norm(v)),1e-12)
        sims = a @ v; order = np.argsort(sims)[-4:]
        weights = np.maximum(sims[order],0)
        out.append(float((sum(weights * np.array([train[i]['y'] for i in order])) + .5)/(sum(weights)+1)))
    return out


def wilson(positive, total):
    if not total:
        return None
    z=1.96; p=positive/total; denominator=1+z*z/total
    center=(p+z*z/(2*total))/denominator
    radius=z*((p*(1-p)/total+z*z/(4*total*total))**.5)/denominator
    return [max(0,center-radius),min(1,center+radius)]


def metrics(rows, probabilities):
    from sklearn.metrics import average_precision_score, brier_score_loss
    y = [r['y'] for r in rows]
    return dict(n=len(y),positive=sum(y),brier=float(brier_score_loss(y,probabilities)),
                average_precision=float(average_precision_score(y,probabilities)) if sum(y) else None)


def evaluate(rows, settings, folds=3, days=7, end=None, target='utility', catboost=False):
    if folds < 2 or days < 1:
        raise ValueError('At least two chronological windows and positive days required')
    end = end or datetime.now(timezone.utc)
    reports = []
    for i in range(folds):
        start = end - timedelta(days=days*(folds-i)); stop = start + timedelta(days=days)
        train = dedup_events(effective(rows,start))
        train_ids = {r['event_id'] for r in train}
        test_all = [r for r in effective(rows,stop) if start <= r['observed_at'] < stop and r['event_id'] not in train_ids]
        test_all = dedup_events(test_all)
        train, train_skipped = eligible(train,target); test, test_skipped = eligible(test_all,target)
        report = dict(start=start.isoformat(),stop=stop.isoformat(),final_holdout=i==folds-1,
                      target=target,train=len(train),test=len(test),train_skipped=train_skipped,test_skipped=test_skipped,
                      motifs=dict(Counter(r['reason'] for r in test_all)),
                      useful_rate_ci95=wilson(sum(r['reason']=='useful' for r in test_all),len(test_all)),
                      categories=dict(Counter(r['assessment'].get('category','unknown') for r in test_all)),models={})
        if test:
            # Reference score proxy is explicit, not an evaluation of unseen articles.
            reference = [max(0,min(1,(r['assessment'].get('ranking',0))/120)) for r in test]
            report['models']['reference_score_proxy'] = metrics(test,reference)
            report['models']['neighbors'] = metrics(test,neighbors(train,test,target))
            if enough(train,settings):
                for backend in (['logistic','catboost'] if catboost else ['logistic']):
                    model,_ = fit(train,backend)
                    probs = model.predict_proba([vector(r['assessment']) for r in test])[:,1].tolist()
                    report['models'][backend] = metrics(test,probs)
            else:
                report['status'] = 'insufficient_training_data'
        else:
            report['status'] = 'no_labeled_test_events'
        reports.append(report)
    return dict(schema_version=1,feature_version=VERSION,folds=reports,
        limitations=['Only presented, voted events are observed; no unbiased full-candidate utility estimate.',
                      'Reference score is a ranking proxy, not a calibrated probability.',
                      'No hyperparameter tuning on final holdout; no automatic promotion.',
                      'Modeled SHAP/coefficients are associations, not causal explanations.',
                      'Reason models are conditional contrasts against useful votes, not independent quality probabilities.'])


def dataset(conn):
    rows = conn.execute('''SELECT c.event_id,c.presentation_id,c.assessment,c.observed_at,
        r.reason,r.imported_at,r.submitted_at,r.id
        FROM feedback_candidates c JOIN feedback_returns r USING(presentation_id)
        WHERE c.synthesized AND c.editions <> '{}' ORDER BY c.observed_at,r.imported_at,r.id''').fetchall()
    return [dict(zip(('event_id','presentation_id','assessment','observed_at','reason','available_at','submitted_at','return_id'),r)) for r in rows]


def atomic_json(path, value):
    import tempfile
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,name = tempfile.mkstemp(prefix='.model-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            f.write(canonical(value)); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('train','evaluate','activate'))
    parser.add_argument('--config'); parser.add_argument('--output',required=True)
    parser.add_argument('--artifact'); parser.add_argument('--target',choices=TARGETS,default='utility')
    parser.add_argument('--catboost',action='store_true'); parser.add_argument('--folds',type=int,default=3)
    parser.add_argument('--days',type=int,default=7)
    args = parser.parse_args(); cfg = load(args.config); settings = cfg['personalization']
    start = time.monotonic()
    if args.action == 'activate':
        if not args.artifact: parser.error('--artifact required')
        value = json.loads(Path(args.artifact).read_text())
        if value.get('version') != VERSION or value.get('backend') != 'logistic' or value.get('target') != 'utility':
            raise ValueError('Only a compatible utility logistic artifact can be activated')
        predict_json(value,{'embedding':[0.]*384})
        destination = Path(args.output)
        if destination.exists():
            atomic_json(str(destination)+'.previous',json.loads(destination.read_text()))
        atomic_json(destination,value)
        print('Artifact activated locally; selection mode remains explicitly configured')
        return
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        rows = dataset(conn)
    now = datetime.now(timezone.utc)
    fingerprint = digest([[r['presentation_id'],r['return_id'],r['assessment'],r['available_at'].isoformat()] for r in rows])
    if args.action == 'evaluate':
        value = evaluate(rows,settings,args.folds,args.days,now,args.target,args.catboost)
    else:
        train,skipped = eligible(dedup_events(effective(rows,now)),args.target)
        if not enough(train,settings):
            print(canonical(dict(status='insufficient_data',events=len(train),classes=dict(Counter(r['y'] for r in train)),skipped=skipped)))
            raise SystemExit(2)
        _,value = fit(train)
        value.update(target=args.target,trained_at=now.isoformat(),n=len(train),skipped=skipped,parameters={'C':1,'solver':'liblinear'})
    import importlib.metadata
    libraries = {}
    for name in ('numpy','scikit-learn','catboost','psycopg'):
        try: libraries[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: pass
    value.update(libraries=libraries,dataset_digest=fingerprint,generated_at=now.isoformat(),elapsed_seconds=time.monotonic()-start,
                 peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    atomic_json(args.output,value)
    print(canonical(dict(output=args.output,elapsed_seconds=value['elapsed_seconds'],peak_rss_kib=value['peak_rss_kib'])))


if __name__ == '__main__':
    main()
