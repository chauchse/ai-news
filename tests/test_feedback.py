"""Feedback protocol, real PHP HTTP receiver and optional disposable PostgreSQL tests."""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import urllib.request

from feedback.common import digest, links, sign, verify, validate_return
from feedback.importer import synchronize, Store, same_submission
from feedback.repository import Repository
from feedback.learning import label, effective, evaluate, fit, predict_json, vector, atomic_json

SECRET = 's' * 40
API = 'a' * 40
PID = digest('presentation')

def row(number=1,reason='useful'):
    stamp = '2026-01-01T00:00:00Z'
    ident = digest(number)
    return dict(schema_version=1,id=ident,submission_id=ident,presentation_id=PID,
                reason=reason,received_at=stamp,submitted_at=stamp)


class ProtocolTests(unittest.TestCase):
    def test_private_bundle_preparation_without_publication(self):
        import io
        from contextlib import redirect_stdout
        from feedback.prepare import main
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'bundle'
            args=['prepare','--private-dir','/private-test/ai-news','--output',str(target)]
            with patch.dict(os.environ,{'FEEDBACK_URL':'https://example.test/feedback/feedback.php',
                    'FEEDBACK_SIGNING_SECRET':SECRET,'FEEDBACK_API_SECRET':API}),patch('sys.argv',args),redirect_stdout(io.StringIO()):
                main()
            public=(target/'public/feedback/feedback.php').read_text()
            private=target/'private/config.php'
            self.assertNotIn(SECRET,public);self.assertNotIn(API,public)
            self.assertIn('/private-test/ai-news/config.php',public)
            self.assertEqual(private.stat().st_mode & 0o777,0o600)
            self.assertTrue((target/'private/pending').is_dir())

    def test_token_signature_expiry_fields(self):
        url = links(PID,'https://example.test/feedback/feedback.php',SECRET,2000000000)[0][1]
        tok = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['token'][0]
        self.assertEqual(verify(tok,SECRET,1900000000)['reason'],'useful')
        for value,secret,now in [(tok+'x',SECRET,1900000000),(tok,'x'*40,1900000000),(tok,SECRET,2100000000)]:
            with self.assertRaises(ValueError): verify(value,secret,now)
        with self.assertRaises(ValueError): links('../','https://example.test',SECRET,2000000000)
        with self.assertRaises(ValueError): links(PID,'http://example.test',SECRET,2000000000)

    def test_replayed_submission_preserves_original_receipt(self):
        a=row(); b=dict(a,received_at='2026-01-02T00:00:00Z')
        self.assertTrue(same_submission(a,b))
        self.assertFalse(same_submission(a,dict(b,reason='off_topic')))
        self.assertEqual(validate_return(a),a)
        with self.assertRaises(ValueError): validate_return(dict(a,id='../bad'))

    def test_import_commit_precedes_ack_and_retries(self):
        class MemoryStore:
            def __init__(self): self.rows={}; self.fail=False
            @contextmanager
            def lock(self): yield
            def save(self,rows):
                if self.fail: raise RuntimeError('commit failed')
                inserted=sum(r['id'] not in self.rows for r in rows)
                self.rows.update({r['id']:r for r in rows})
                return [r['id'] for r in rows],inserted
        store=MemoryStore(); pending=[row()]; calls=[]
        def request(action,payload=None):
            calls.append(action)
            if action=='pull': return dict(schema_version=1,returns=list(pending),invalid_count=1)
            self.assertTrue(all(i in store.rows for i in payload['ids']))
            pending.clear()
            return dict(schema_version=1,deleted=payload['ids'],absent=[],errors=[])
        client=Mock(); client.request.side_effect=request
        cfg=dict(max_batches=3,batch_size=500)
        store.fail=True
        with self.assertRaises(RuntimeError): synchronize(client,store,cfg)
        self.assertEqual(calls,['pull'])
        store.fail=False; calls.clear()
        result=synchronize(client,store,cfg)
        self.assertEqual(result['imported'],1); self.assertEqual(result['confirmed'],1)
        pending.append(row()); result=synchronize(client,store,cfg)
        self.assertEqual(result['known'],1)
        self.assertEqual(len(store.rows),1)

    def test_ack_loss_and_mismatch_do_not_erase_local_data(self):
        store=Mock(); store.lock.return_value.__enter__=Mock(); store.lock.return_value.__exit__=Mock(return_value=False)
        store.save.return_value=([row()['id']],1)
        client=Mock(); client.request.side_effect=[dict(schema_version=1,returns=[row()]), OSError('lost')]
        with self.assertRaises(OSError): synchronize(client,store,dict(max_batches=1,batch_size=10))
        store.save.assert_called_once()

    def test_mail_links_not_spoken_or_in_podcast(self):
        from mailer.send import build_message
        from audio.tts import extract
        from podcast.publish import parse_articles
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'briefing.md'
            text='### 1. Title\nSummary.\n**Sources :**\n- [Source](https://example.test)\n<!-- feedback:'+PID+' -->\n'
            path.write_text(text)
            cfg=Path(directory)/'feedback.yaml'; cfg.write_text('enabled: true\nlink_days: 90\n')
            with patch.dict(os.environ,{'FEEDBACK_CONFIG':str(cfg),'FEEDBACK_URL':'https://example.test/feedback/feedback.php','FEEDBACK_SIGNING_SECRET':SECRET}):
                msg=build_message(dict(input=str(path),sender='a@example.test',recipient='b@example.test'))
            self.assertIn('Votre avis',msg.get_body(('plain',)).get_content())
            self.assertIn('action=vote',msg.get_body(('html',)).get_content())
            self.assertEqual(extract(text)[0][2],'Summary.')
            self.assertEqual(len(parse_articles(text)[0]['sources']),1)


PHP = os.getenv('TEST_PHP') or shutil.which('php')

@unittest.skipUnless(PHP, 'Set TEST_PHP to run real receiver integration')
class PHPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); cls.root=Path(cls.tmp.name)
        cls.pending=cls.root/'private'/'pending'; cls.pending.mkdir(parents=True)
        cls.web=cls.root/'www'; cls.web.mkdir()
        shutil.copy('web/public/feedback.php',cls.web/'feedback.php')
        cls.config=cls.root/'private'/'config.php'
        cls.config.write_text("<?php return "+"['signing_secret'=>'"+SECRET+"','api_secret'=>'"+API+"','pending_dir'=>'"+str(cls.pending)+"','max_files'=>10,'max_file_bytes'=>4096,'max_request_bytes'=>65536,'max_batch'=>500,'form_seconds'=>1800];")
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        cls.url=f'http://127.0.0.1:{port}/feedback.php'
        env=dict(os.environ,FEEDBACK_PRIVATE_CONFIG=str(cls.config))
        cls.process=subprocess.Popen([PHP,'-n','-S',f'127.0.0.1:{port}','-t',str(cls.web)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try: urllib.request.urlopen(cls.url,timeout=.2)
            except urllib.error.HTTPError: break
            except OSError: time.sleep(.02)
        else: raise RuntimeError('PHP did not start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate(); cls.process.wait(timeout=5); cls.tmp.cleanup()

    def setUp(self):
        for p in self.pending.iterdir():
            if p.name!='.lock': p.unlink()

    def request(self,action,body=None,auth=False):
        headers={'Authorization':'Bearer '+API} if auth else {}
        if isinstance(body,dict):
            if action=='ack': body=json.dumps(body).encode(); headers['Content-Type']='application/json'
            else: body=urllib.parse.urlencode(body).encode(); headers['Content-Type']='application/x-www-form-urlencoded'
        req=urllib.request.Request(self.url+'?action='+action,data=body,headers=headers)
        with urllib.request.urlopen(req) as response: return response.read().decode()

    def form(self,reason='useful'):
        p=dict(v=1,kind='link',presentation_id=PID,reason=reason,link_id=digest(reason),exp=int(time.time())+86400)
        body=self.request('vote&token='+urllib.parse.quote(sign(p,SECRET)))
        forms=re.findall(r'name="token" value="([^"]+)"',body)
        return forms,body

    def vote(self,index=0):
        forms,_=self.form(); self.request('vote',{'token':forms[index]})
        return verify(forms[index],SECRET)['submission_id']

    def test_get_post_double_correction_and_replay_after_purge(self):
        forms,body=self.form()
        self.assertIn('Confirmer',body); self.assertEqual(list(self.pending.glob('*.json')),[])
        self.request('vote',{'token':forms[0]}); self.request('vote',{'token':forms[0]})
        self.assertEqual(len(list(self.pending.glob('*.json'))),1)
        old=verify(forms[0],SECRET)['submission_id']
        with self.assertRaises(urllib.error.HTTPError): self.request('vote',{'token':forms[1]})
        new=self.vote(1); self.assertNotEqual(old,new)
        response=json.loads(self.request('pull',auth=True)); self.assertEqual(len(response['returns']),2)
        for r in response['returns']: validate_return(r)
        ack=json.loads(self.request('ack',{'ids':[old]},True)); self.assertEqual(ack['deleted'],[old])
        self.assertTrue((self.pending/(new+'.json')).exists())
        ack=json.loads(self.request('ack',{'ids':[old]},True)); self.assertEqual(ack['absent'],[old])
        self.request('vote',{'token':forms[0]})
        self.assertEqual(len(list(self.pending.glob('*.json'))),2)

    def test_direct_vote_config_replay_and_explicit_correction(self):
        original=self.config.read_text()
        self.config.write_text(original.replace("['signing_secret'", "['require_confirmation'=>false,'signing_secret'"))
        try:
            p=dict(v=1,kind='link',presentation_id=PID,reason='useful',link_id=digest('direct'),exp=int(time.time())+86400)
            query='vote&token='+urllib.parse.quote(sign(p,SECRET))
            body=self.request(query);self.assertIn('enregistré',body)
            first=json.loads(self.request('pull',auth=True))['returns'][0]
            self.request(query)
            self.assertEqual(len(list(self.pending.glob('*.json'))),1)
            self.request('ack',{'ids':[first['id']]},True);self.request(query)
            replay=json.loads(self.request('pull',auth=True))['returns'][0]
            self.assertTrue(same_submission(first,replay))
            body=self.request(query+'&confirm=1')
            forms=re.findall(r'name="token" value="([^"]+)"',body)
            self.assertEqual(len(forms),6)
            self.request('vote',{'token':forms[1]})
            self.assertEqual(len(list(self.pending.glob('*.json'))),2)
        finally:
            self.config.write_text(original)

    def test_auth_bounds_invalid_files_temporary_and_exact_ack(self):
        with self.assertRaises(urllib.error.HTTPError): self.request('pull')
        with self.assertRaises(urllib.error.HTTPError): self.request('pull&limit=501',auth=True)
        first=self.vote(); batch=json.loads(self.request('pull',auth=True))
        second=self.vote(1)
        invalid=self.pending/(digest('bad')+'.json'); invalid.write_text('{')
        (self.pending/'.writing-incomplete').write_text('{')
        value=json.loads(self.request('pull',auth=True))
        self.assertEqual(value['invalid_count'],1); self.assertEqual(len(value['returns']),2)
        self.request('ack',{'ids':[r['id'] for r in batch['returns']]},True)
        self.assertFalse((self.pending/(first+'.json')).exists())
        self.assertTrue((self.pending/(second+'.json')).exists()); self.assertTrue(invalid.exists())
        with self.assertRaises(urllib.error.HTTPError): self.request('ack',{'ids':['../config']},True)

    def test_invalid_signature_expired_and_post_without_form(self):
        with self.assertRaises(urllib.error.HTTPError): self.request('vote&token=bad')
        p=dict(v=1,kind='link',presentation_id=PID,reason='useful',link_id=digest('x'),exp=int(time.time())-1)
        with self.assertRaises(urllib.error.HTTPError): self.request('vote&token='+urllib.parse.quote(sign(p,SECRET)))
        p['exp']=int(time.time())+500
        with self.assertRaises(urllib.error.HTTPError): self.request('vote',{'token':sign(p,SECRET)})

    def test_storage_capacity_does_not_purge_votes(self):
        for _ in range(10): self.vote()
        with self.assertRaises(urllib.error.HTTPError) as error: self.vote()
        self.assertEqual(error.exception.code,507)
        self.assertEqual(len(list(self.pending.glob('*.json'))),10)

    def test_stolen_link_cannot_fill_queue_with_corrections(self):
        original=self.config.read_text()
        self.config.write_text(original.replace("'max_files'=>10", "'max_files'=>100,'max_pending_per_presentation'=>3"))
        try:
            for _ in range(3):self.vote()
            with self.assertRaises(urllib.error.HTTPError) as error:self.vote()
            self.assertEqual(error.exception.code,429)
            self.assertEqual(len(list(self.pending.glob('*.json'))),3)
        finally:self.config.write_text(original)

    def test_cross_site_post_and_malformed_input_rejected(self):
        forms,_=self.form()
        req=urllib.request.Request(self.url+'?action=vote',
            data=urllib.parse.urlencode({'token':forms[0]}).encode(),
            headers={'Sec-Fetch-Site':'cross-site','Content-Type':'application/x-www-form-urlencoded'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
        self.assertEqual(error.exception.code,403)
        self.assertEqual(list(self.pending.glob('*.json')),[])
        for action in ('vote&token[]=x','vote&token='+('x'*8200),'vote&token=%3Cscript%3E'):
            with self.assertRaises(urllib.error.HTTPError) as error:self.request(action)
            body=error.exception.read().decode()
            self.assertNotIn(str(self.root),body)
            self.assertNotIn('<script>',body)

    @unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'Requires disposable PostgreSQL')
    def test_real_php_pull_postgres_commit_and_public_purge(self):
        import psycopg
        from feedback.importer import Client
        schema='php_feedback_'+os.urandom(8).hex()
        with psycopg.connect(os.environ['TEST_DATABASE_URL']) as conn:
            conn.execute('CREATE SCHEMA '+schema); conn.execute('SET search_path TO '+schema)
            conn.execute(Path('app/sql/005_feedback.sql').read_text()); conn.commit()
            try:
                first=self.vote(); second=self.vote(1)
                cfg=dict(endpoint='https://example.test/feedback.php',batch_size=500,max_batches=5,
                         http_timeout=5,max_response_bytes=2097152)
                client=Client(cfg,API)
                client.url=self.url # HTTP only for loopback integration fixture.
                stats=synchronize(client,Store(conn),cfg)
                self.assertEqual(stats['imported'],2)
                self.assertEqual(list(self.pending.glob('*.json')),[])
                count=conn.execute('SELECT count(*) FROM feedback_returns').fetchone()[0]; conn.commit()
                self.assertEqual(count,2)
                self.assertEqual(synchronize(client,Store(conn),cfg)['received'],0)
            finally:
                conn.rollback(); conn.execute('DROP SCHEMA '+schema+' CASCADE'); conn.commit()


class LearningTests(unittest.TestCase):
    def data(self):
        base=datetime(2026,1,1,tzinfo=timezone.utc); rows=[]
        for i in range(60):
            reason='useful' if i%2 else 'off_topic'; emb=[0.]*384; emb[i%2]=1.
            rows.append(dict(event_id=digest(i),presentation_id=digest(['p',i]),return_id=digest(['r',i]),
                observed_at=base+timedelta(days=i),available_at=base+timedelta(days=i,hours=1),submitted_at=base+timedelta(days=i,hours=1),
                reason=reason,assessment=dict(embedding=emb,ranking=80 if i%2 else 30)))
        return rows

    def test_labels_do_not_invent_other_dimensions(self):
        self.assertIsNone(label('already_covered','utility'))
        self.assertIsNone(label('off_topic','shallow_or_promotional'))
        self.assertEqual(label('useful','utility'),1)
        self.assertEqual(label('not_new','not_new'),1)

    def test_future_corrections_and_event_leakage(self):
        rows=self.data(); cutoff=rows[10]['observed_at']
        self.assertTrue(all(r['available_at']<cutoff for r in effective(rows,cutoff)))
        later=dict(rows[0],reason='off_topic',available_at=cutoff+timedelta(days=1),submitted_at=cutoff+timedelta(days=1),return_id=digest('later'))
        self.assertEqual(effective(rows+[later],cutoff)[0]['reason'],rows[0]['reason'])
        settings=dict(min_training_events=2,min_class_events=1)
        report=evaluate(rows,settings,folds=3,days=10,end=rows[-1]['observed_at']+timedelta(days=1))
        self.assertTrue(report['folds'][-1]['final_holdout'])
        self.assertIn('logistic',report['folds'][-1]['models'])
        one=evaluate([r for r in rows if r['reason']=='useful'],settings,folds=2,days=10,end=rows[-1]['observed_at'])
        self.assertNotIn('logistic',one['folds'][-1]['models'])
        repeated=[dict(r,event_id='same') for r in rows]
        repeated_report=evaluate(repeated,settings,folds=2,days=10,end=rows[-1]['observed_at'])
        self.assertTrue(all(f['test']==0 for f in repeated_report['folds']))

    def test_json_artifact_matches_logistic_and_atomic_failure(self):
        rows=[dict(r,y=label(r['reason'],'utility')) for r in self.data()]
        model,artifact=fit(rows)
        for row_ in rows[:4]:
            expected=model.predict_proba([vector(row_['assessment'])])[0,1]
            self.assertAlmostEqual(predict_json(artifact,row_['assessment']),expected)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'active.json'; atomic_json(path,artifact); before=path.read_bytes()
            with patch('feedback.learning.os.replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError): atomic_json(path,{'bad':'new'})
            self.assertEqual(path.read_bytes(),before)

    def test_catboost_cpu_candidate(self):
        rows=[dict(r,y=label(r['reason'],'utility')) for r in self.data()]
        try: import catboost
        except ImportError: self.skipTest('Optional catboost not installed')
        model,artifact=fit(rows,'catboost')
        self.assertEqual(artifact['backend'],'catboost')
        self.assertGreater(model.predict_proba([vector(rows[1]['assessment'])])[0,1],.5)


class PersonalizationTests(unittest.TestCase):
    def test_no_votes_cannot_change_ranking_even_if_llm_requests_it(self):
        import numpy as np
        from feedback.personalize import apply,FEATURES
        repo=Mock();repo.annotated.return_value=[];repo.cache.return_value=None
        article=dict(title='Candidate title',summary='Source content.',score=50,event_id=digest('candidate'),presentation_id=PID)
        item=dict(group=[article],ranking=60)
        features={k:{'value':'unknown','evidence':''} for k in FEATURES}
        ask=Mock(return_value=dict(features=features,adjustment=1,reason='No evidence'))
        model=Mock();model.encode.return_value=np.array([[1.]+[0.]*383])
        apply([item],repo,{'mode':'shadow'},{'model':'local'},lambda:model,ask)
        self.assertEqual(item['personalized_ranking'],60)
        self.assertEqual(len(item['embedding']),384)
        self.assertEqual(item['personalization']['mode'],'shadow')
    def test_invalid_evidence_falls_back_and_keeps_embedding(self):
        import numpy as np
        from feedback.personalize import apply,FEATURES
        repo=Mock();repo.annotated.return_value=[dict(event_id=digest('old'),presentation_id=digest('oldp'),submitted_at=datetime(2026,1,1,tzinfo=timezone.utc),reason='off_topic',snapshot={'articles':[dict(title='Old',summary='Source.',score=50)]})];repo.cache.return_value=None
        article=dict(title='Candidate',summary='Source.',score=50,event_id=digest('candidate'),presentation_id=PID)
        item=dict(group=[article],ranking=60)
        features={k:{'value':'claim','evidence':'invented quotation'} for k in FEATURES}
        model=Mock();model.encode.return_value=np.array([[1.]+[0.]*383,[1.]+[0.]*383])
        apply([item],repo,{'mode':'shadow'},{'model':'local'},lambda:model,Mock(return_value=dict(features=features,adjustment=1,reason='bad')))
        self.assertNotIn('personalized_ranking',item)
        self.assertIn('error',item['personalization'])
        self.assertEqual(len(item['embedding']),384)
    def test_shadow_keeps_reference_active_is_explicit(self):
        from collections import Counter
        tree=ast.parse(Path('embeddings/briefing.py').read_text())
        funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('select_groups','choose_assessed')]
        ns=dict(Counter=Counter,embedding_model=Mock(),ollama_object=Mock(),near_duplicate_topic=lambda a,b:False)
        exec(compile(ast.Module(body=funcs,type_ignores=[]),'<selection>','exec'),ns)
        def item(number,ranking,proposed):
            return dict(group=[dict(presentation_id=digest(number),title='Candidate '+str(number),score=50)],ranking=ranking,personalized_ranking=proposed,
                        category='artificial_intelligence',technology=3,importance=3,business=0,priority=False,watch_priority=0)
        a,b=item(1,100,10),item(2,50,110)
        ns['business_assessments']=lambda groups,cfg:[a,b]
        repo=Mock();repo.briefing_id=digest('day')
        cfg={'briefing':{'max_events':1,'selection':{'artificial_intelligence':{'target':1}}},
             'llm':{'model':'local'},'_feedback_repository':repo,
             '_feedback_settings':{'personalization':{'mode':'shadow'}}}
        with patch('feedback.personalize.apply'):
            selected,_=ns['select_groups']([],cfg); self.assertEqual(selected,[a])
            cfg['_feedback_settings']['personalization']['mode']='active'
            selected,_=ns['select_groups']([],cfg); self.assertEqual(selected,[b])
    def test_discovery_preserves_categories_and_critical(self):
        from feedback.exploration import discover
        def item(number,critical=False):
            return dict(group=[dict(presentation_id=digest(number),id=number,url='https://example.test/'+str(number))],
                        category='security',technology=3,importance=3,priority=critical)
        critical,old,new=item(1,True),item(2),item(3)
        selected,report=discover([critical,old,new],[critical,old],{'briefing':{}},digest('day'))
        self.assertIn(critical,selected);self.assertIn(new,selected)
        self.assertEqual(report['probability'],1.)


class DedupTests(unittest.TestCase):
    def namespace(self):
        import unicodedata
        tree=ast.parse(Path('embeddings/briefing.py').read_text())
        functions={'canonical_url','normalized_title','distinctive_tokens','identifiers','vulnerability_identity',
                   'same_vulnerability','same_event','group_category','deduplicate_security_events'}
        constants={'STOPWORDS','PRODUCT_PATTERNS','VERSION_PATTERN','ZDI_PATTERN'}
        nodes=[n for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name in functions) or
               (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in constants for t in n.targets))]
        import sys
        ns=dict(re=re,unicodedata=unicodedata,THRESHOLD=.8,sys=sys,
                urlsplit=urllib.parse.urlsplit,urlunsplit=urllib.parse.urlunsplit,parse_qsl=urllib.parse.parse_qsl,urlencode=urllib.parse.urlencode,
                extract_cves=lambda text:set(re.findall(r'CVE-\d{4}-\d+',text)),
                ollama_object=Mock(return_value={'same_event':True,'confidence':.99,'evidence':'Same SEV-SNP advisory and date'}))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<dedup>','exec'),ns)
        return ns
    def article(self,title,security=True):
        return dict(title=title,summary='Same advisory',score=65,category='security' if security else 'artificial_intelligence',
                    semantic_similarity=.9,url='https://example.test/'+digest(title),vulnerability={'candidate':security})
    def test_amd_bilingual_review_and_distinct_cves(self):
        ns=self.namespace();cfg={'briefing':{'business_triage':{'max_dedup_reviews':8}},'llm':{'model':'test'}}
        a=self.article('AMD EPYC Processors : élévation de privilèges via SEV-SNP, analysé le 11/08/2026')
        b=self.article('AMD EPYC Processors: privilege escalation via SEV-SNP, analyzed on 11/08/2026')
        self.assertFalse(ns['same_event'](a,b,.99))
        self.assertEqual(len(ns['deduplicate_security_events']([[a],[b]],cfg)),1)
        a['summary']='CVE-2026-11111'; b['summary']='CVE-2026-22222';ns['ollama_object'].reset_mock()
        self.assertEqual(len(ns['deduplicate_security_events']([[a],[b]],cfg)),2)
        ns['ollama_object'].assert_not_called()
    def test_same_url_distinct_advisories_have_distinct_identity_keys(self):
        from feedback.repository import source_key
        a=self.article('AMD EPYC CVE-2026-11111');b=self.article('AMD EPYC CVE-2026-22222')
        b['url']=a['url']
        self.assertNotEqual(source_key(a),source_key(b))
        c=dict(a,summary='cve-2026-11111')
        self.assertEqual(source_key(a),source_key(c))

    def test_general_bilingual_event_arbitration_is_bounded(self):
        ns=self.namespace();cfg={'briefing':{'business_triage':{'max_dedup_reviews':0}},'llm':{'model':'test'}}
        a=self.article('Anthropic Claude false murder report',False)
        b=self.article('Anthropic Claude faux signalement de meurtre',False)
        self.assertEqual(len(ns['deduplicate_security_events']([[a],[b]],cfg)),2)
        ns['ollama_object'].assert_not_called()
        cfg['briefing']['business_triage']['max_dedup_reviews']=1
        self.assertEqual(len(ns['deduplicate_security_events']([[a],[b]],cfg)),1)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'Requires a disposable PostgreSQL database')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.conn=psycopg.connect(os.environ['TEST_DATABASE_URL'])
        self.schema='test_feedback_'+os.urandom(8).hex()
        self.conn.execute('CREATE SCHEMA '+self.schema)
        self.conn.execute('SET search_path TO '+self.schema)
        self.conn.execute(Path('app/sql/005_feedback.sql').read_text()); self.conn.commit()
    def tearDown(self):
        self.conn.rollback(); self.conn.execute('DROP SCHEMA '+self.schema+' CASCADE'); self.conn.commit(); self.conn.close()
    def test_identity_trace_unknown_and_late_replay(self):
        repo=Repository(self.conn,'2026-01-01',{})
        a=dict(id=1,title='FR title',summary='Texte',url='https://example.test/a?utm_source=rss',score=50)
        group=[a]; repo.register([group]); pid=a['presentation_id']; event=a['event_id']
        b=dict(a,title='EN title',url='https://example.test/a',id=2)
        repo.register([[b]]); self.assertEqual(pid,b['presentation_id']); self.assertEqual(event,b['event_id'])
        store=Store(self.conn); vote=dict(row(),presentation_id=pid)
        with store.lock():
            self.assertEqual(store.save([vote])[1],1)
            self.assertEqual(store.save([dict(vote,received_at='2026-01-02T00:00:00Z')])[1],0)
            self.assertEqual(store.save([row(2)])[1],1) # unknown presentation preserved
        repo.assess([dict(group=group,technology=3,importance=2)],[],[])
        repo.stage(group,'synthesized'); repo.stage(group,'edition','en'); repo.stage(group,'edition','fr')
        record=self.conn.execute('SELECT editions FROM feedback_candidates WHERE presentation_id=%s',(pid,)).fetchone()[0]; self.conn.commit()
        self.assertEqual(record,{'en':True,'fr':True})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM feedback_returns').fetchone()[0],2); self.conn.commit()
        before=self.conn.execute('SELECT payload FROM feedback_returns WHERE id=%s',(vote['id'],)).fetchone()[0]; self.conn.commit()
        self.assertEqual(before['received_at'],vote['received_at'])
        self.assertEqual(store.save([dict(vote,reason='off_topic')]),([],0))
        self.conn.rollback()

    def test_training_cli_insufficient_preserves_existing_artifact(self):
        import io
        from contextlib import redirect_stdout
        from feedback.learning import main
        from psycopg.conninfo import make_conninfo
        with tempfile.TemporaryDirectory() as directory:
            cfg=Path(directory)/'cfg.yaml';cfg.write_text('enabled: false\npersonalization: {min_training_events: 2, min_class_events: 1}\n')
            artifact=Path(directory)/'candidate.json';artifact.write_text('{"previous":true}')
            dsn=make_conninfo(os.environ['TEST_DATABASE_URL'],options='-c search_path='+self.schema)
            with patch.dict(os.environ,{'DATABASE_URL':dsn}),patch('sys.argv',['learning','train','--config',str(cfg),'--output',str(artifact)]),redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exc: main()
            self.assertEqual(exc.exception.code,2)
            self.assertEqual(artifact.read_text(),'{"previous":true}')

    def test_training_evaluation_and_activation_cli_on_synthetic_data(self):
        import io
        from contextlib import redirect_stdout
        from feedback.learning import main
        from psycopg.conninfo import make_conninfo
        repo=Repository(self.conn,'2026-01-01',{})
        synthetic=LearningTests().data()
        from psycopg.types.json import Jsonb
        for sample in synthetic:
            event=sample['event_id'];pid=sample['presentation_id'];assessment=dict(sample['assessment'],technology=3,importance=3,business=0)
            self.conn.execute('INSERT INTO feedback_events(id) VALUES (%s)',(event,))
            self.conn.execute("INSERT INTO feedback_candidates(presentation_id,briefing_id,event_id,snapshot,assessment,observed_at,synthesized,editions) VALUES (%s,%s,%s,'{}',%s,%s,TRUE,'{\"en\":true}')",
                (pid,repo.briefing_id,event,Jsonb(assessment),sample['observed_at']))
            r=dict(row(sample['return_id']),presentation_id=pid,reason=sample['reason'])
            self.conn.execute('INSERT INTO feedback_returns(id,submission_id,presentation_id,reason,submitted_at,received_at,imported_at,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (r['id'],r['id'],pid,r['reason'],sample['submitted_at'],sample['submitted_at'],sample['available_at'],Jsonb(r)))
        self.conn.commit()
        dsn=make_conninfo(os.environ['TEST_DATABASE_URL'],options='-c search_path='+self.schema)
        with tempfile.TemporaryDirectory() as directory:
            cfg=Path(directory)/'cfg.yaml';cfg.write_text('enabled: false\npersonalization: {min_training_events: 2, min_class_events: 1}\n')
            artifact=Path(directory)/'candidate.json';active=Path(directory)/'active.json';report=Path(directory)/'evaluation.json'
            with patch.dict(os.environ,{'DATABASE_URL':dsn}),redirect_stdout(io.StringIO()):
                for args in (['train','--output',str(artifact)],['activate','--artifact',str(artifact),'--output',str(active)],['evaluate','--output',str(report)]):
                    with patch('sys.argv',['learning',*args,'--config',str(cfg)]): main()
            self.assertEqual(json.loads(active.read_text())['n'],60)
            self.assertEqual(json.loads(report.read_text())['feature_version'],'features-v1-384-64')
            self.assertTrue(json.loads(report.read_text())['folds'][-1]['final_holdout'])


if __name__=='__main__': unittest.main()
