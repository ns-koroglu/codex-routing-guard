import copy
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('staged_dispatch',Path(__file__).with_name('dispatch.py'))
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.catalog={'hostId':'local','source':'isolated supported-tool fixture','observedAt':time.time(),'maxAgeSeconds':86400,
                      'models':{'gpt-6-luna':['low','medium','high'],'gpt-6.1-sol':['low','medium','high'],'gpt-6-astra':['low','high']}}
        self.context=d.resolve_project([],str(self.root))
        self.prompt='EXECUTION_MODE: ROUTED_WORK_V1\nPerform only the fixture task.'
        self.data={};self.key=self.reserve();self.record=self.data[self.key]

    def reserve(self,task='task-A',source='source',request='request-1',context=None,projects=None):
        return d.reserve(self.data,source,request if task=='task-A' else request+':'+task,'gpt-6.1-sol','high',context or self.context,self.prompt,
                         projects or [],task_id=task,catalog=self.catalog)

    def attach(self):d.attach(self.record,'destination','local',data=self.data)

    def runtime(self,model='gpt-6.1-sol',effort='high',turn='turn-1',tail='',identity='destination',parent=None):
        path=self.root/(identity+'.jsonl')
        session={'id':identity,'source':{'subagent':{'thread_spawn':{'parent_thread_id':parent}}} if parent else 'vscode'}
        events=[{'type':'session_meta','payload':session},{'type':'turn_context','timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'payload':{'turn_id':turn,'model':model,'effort':effort,'cwd':str(self.root)}}]
        path.write_text('\n'.join(json.dumps(x) for x in events)+'\n'+tail);return path

    def verified(self):
        self.attach();self.log=self.runtime();d.verify(self.record,self.log)

    def snapshot(self,state='inProgress',**kw):
        return {'threadId':'destination','hostId':'local','projectId':None,'turnId':'turn-1','turnStatus':state,
                'threadStatus':'active' if state=='inProgress' else 'idle','observedAt':time.time(),**kw}

    def completion(self):
        self.verified();artifact=self.root/'result.txt';artifact.write_text('Verified fixture result')
        start={'runtimeFile':str(self.log),'turnId':'turn-1','reviewerThreadId':'destination',
               'artifactSha256':{str(artifact):hashlib.sha256(artifact.read_bytes()).hexdigest()}}
        d.begin_review(self.record,start)
        return {'app':self.snapshot('completed'),'review':{**self.record['reviewRequest'],'verdict':'approved'}}

    def test_stable_identity_rejects_new_turn_duplicate(self):
        with self.assertRaises(ValueError):self.reserve(request='new-runtime-turn')
        self.assertEqual(len(self.data),1)

    def test_unrelated_identical_prompt_can_start_distinct_task(self):
        self.assertNotEqual(self.reserve(task='task-B'),self.key)

    def test_execution_child_cannot_reroute_same_task(self):
        self.attach()
        with self.assertRaises(ValueError):self.reserve(source='destination')

    def test_cycle_or_destination_reuse_is_rejected(self):
        self.attach();key=self.reserve(task='task-B',source='destination')
        with self.assertRaises(ValueError):d.attach(self.data[key],'source','local',data=self.data)
        with self.assertRaises(ValueError):d.attach(self.data[key],'destination','local',data=self.data)

    def test_uncertain_reconciliation_reuses_original_destination(self):
        self.attach();d.block(self.record,'Create outcome uncertain')
        d.reconcile(self.record,'destination','local','App discovered original',data=self.data)
        self.assertEqual(self.record['status'],'queued')
        d.block(self.record,'retry observation')
        with self.assertRaises(ValueError):d.reconcile(self.record,'other','local','App evidence',data=self.data)

    def test_destination_validation_has_no_partial_mutation(self):
        original=copy.deepcopy(self.record)
        for host,project in [('remote',None),('local','unexpected')]:
            with self.assertRaises(ValueError):d.attach(self.record,'destination',host,project,data=self.data)
            self.assertEqual(original,self.record)

    def test_metadata_only_means_profile_verified(self):
        self.verified();self.assertEqual(self.record['status'],'profile_verified')
        d.observe(self.record,self.snapshot());self.assertEqual(self.record['status'],'running')

    def test_profile_mismatch_invalidates_previous_running_claim(self):
        self.verified();d.observe(self.record,self.snapshot());d.verify(self.record,self.runtime('gpt-6-luna','low'))
        self.assertEqual(self.record['status'],'blocked');self.assertNotIn('actualModel',self.record)

    def test_partial_runtime_line_is_pending_but_corruption_blocks(self):
        self.attach();d.verify(self.record,self.runtime(tail='{"type":'))
        self.assertEqual(self.record['status'],'queued');self.assertIn('verificationPending',self.record)
        d.verify(self.record,self.runtime(tail='{bad}\n'))
        self.assertEqual(self.record['status'],'blocked')

    def test_wrong_session_and_stale_app_evidence_cannot_start(self):
        self.attach();d.verify(self.record,self.runtime(identity='wrong'));self.assertEqual(self.record['status'],'blocked')
        d.reconcile(self.record,'destination','local','actual identity known',data=self.data)
        d.verify(self.record,self.runtime())
        for changes in ({'observedAt':time.time()-600},{'turnId':'different'},{'threadId':'other'},{'projectId':'other'}):
            with self.assertRaises(ValueError):d.observe(self.record,self.snapshot(**changes))
        self.assertEqual(self.record['status'],'profile_verified')

    def test_completed_requires_registered_actual_strong_review(self):
        self.verified()
        with self.assertRaises(ValueError):d.finish(self.record,{'app':self.snapshot('completed'),'review':{'verdict':'approved'}})

    def test_actual_review_and_completion_can_finish(self):
        evidence=self.completion();d.finish(self.record,evidence);self.assertEqual(self.record['status'],'completed')
        for action in (lambda:d.block(self.record,'reopen'),lambda:d.reconcile(self.record,'destination','local','reopen'),lambda:d.related(self.record,'r','followup')):
            with self.assertRaises(ValueError):action()

    def test_review_rejected_after_followup_revision(self):
        evidence=self.completion();d.related(self.record,'new-user-message','followup')
        self.assertEqual(self.record['revision'],2);self.assertNotIn('reviewRequest',self.record)
        d.verify(self.record,self.runtime())
        with self.assertRaises(ValueError):d.finish(self.record,evidence)

    def test_old_runtime_and_reviewer_cannot_be_reregistered_for_new_revision(self):
        evidence=self.completion();old_review=copy.deepcopy(evidence['review'])
        d.related(self.record,'new-user-message','followup')
        d.verify(self.record,self.log);self.assertEqual(self.record['status'],'blocked')
        d.reconcile(self.record,'destination','local','Current destination known',data=self.data)
        old_log=self.root/'old-review.jsonl';old_log.write_bytes(self.log.read_bytes())
        d.verify(self.record,self.runtime(turn='turn-2'))
        with self.assertRaises(ValueError):d.begin_review(self.record,{**old_review,'runtimeFile':str(old_log)})

    def test_approval_wait_cannot_be_bypassed_by_generic_followup(self):
        self.verified();d.observe(self.record,self.snapshot('waitingForApproval'))
        self.assertEqual(self.record['status'],'waiting_for_user')
        before=copy.deepcopy(self.record)
        with self.assertRaises(ValueError):d.related(self.record,'generic-continue','followup')
        self.assertEqual(before,self.record)

    def test_arbitrary_runtime_ancestor_is_not_a_verified_checkout(self):
        project={'projectId':'p','projectKind':'local','path':str(self.root),'hostId':'local'}
        context=d.resolve_project([project],str(self.root));key=self.reserve(task='ancestor',context=context,projects=[project]);record=self.data[key]
        d.attach(record,'destination','local','p',self.data);path=self.runtime()
        lines=[json.loads(x) for x in path.read_text().splitlines()];lines[-1]['payload']['cwd']='/';path.write_text('\n'.join(json.dumps(x) for x in lines)+'\n')
        d.verify(record,path);self.assertEqual(record['status'],'blocked')

    def test_legacy_original_request_reuses_mapping_without_mutating_history(self):
        legacy={'source':'legacy-source','requestId':'original-user-request','destination':'legacy-destination','status':'started','context':self.context}
        self.data['legacy-key']=copy.deepcopy(legacy)
        with self.assertRaises(ValueError):
            d.reserve(self.data,'legacy-source','original-user-request','gpt-6.1-sol','high',self.context,self.prompt,[],task_id='new-v2-id',catalog=self.catalog)
        key,found=d.lookup_request(self.data,'legacy-source','original-user-request')
        self.assertEqual(key,'legacy-key');self.assertEqual(found,legacy)
        self.reserve(task='genuinely-new',source='legacy-source',request='different-user-request')

    def test_changed_artifacts_or_failed_completion_are_rejected(self):
        evidence=self.completion();path=next(iter(evidence['review']['artifactSha256']));Path(path).write_text('changed')
        with self.assertRaises(ValueError):d.finish(self.record,evidence)
        evidence['app']['error']='failure'
        with self.assertRaises(ValueError):d.finish(self.record,evidence)

    def test_child_review_must_belong_to_destination(self):
        self.verified();log=self.runtime(identity='child',parent='wrong')
        with self.assertRaises(ValueError):d.begin_review(self.record,{'runtimeFile':str(log),'turnId':'turn-1','reviewerThreadId':'child','resultSha256':'abc'})
        log=self.runtime(identity='child',parent='destination');d.begin_review(self.record,{'runtimeFile':str(log),'turnId':'turn-1','reviewerThreadId':'child','resultSha256':'abc'})
        d.finish(self.record,{'app':self.snapshot('completed',finalMessageSha256='abc'),'review':{**self.record['reviewRequest'],'verdict':'approved'}})
        self.assertEqual(self.record['status'],'completed')

    def test_stop_is_request_until_app_confirms(self):
        self.verified();d.related(self.record,'stop-message','stop');self.assertEqual(self.record['status'],'stop_requested')
        d.observe(self.record,self.snapshot());self.assertEqual(self.record['status'],'stop_requested')
        d.observe(self.record,self.snapshot('interrupted'));self.assertEqual(self.record['status'],'paused')
        with self.assertRaises(ValueError):d.related(self.record,'resume-text','followup')

    def test_cancelled_task_is_terminal(self):
        self.verified();d.related(self.record,'cancel-message','cancel');d.observe(self.record,self.snapshot('interrupted'))
        self.assertEqual(self.record['status'],'cancelled')
        with self.assertRaises(ValueError):d.block(self.record,'reopen')

    def test_approval_wait_requires_explicit_current_revision_provenance(self):
        self.attach();d.related(self.record,'audit-result','wait_approval')
        with self.assertRaises(ValueError):d.reconcile(self.record,'destination','local','resume')
        with self.assertRaises(ValueError):d.authorize(self.record,{'kind':'explicit_user_approval'})
        evidence={'kind':'explicit_user_approval','taskId':'task-A','revision':1,'messageId':'user-approve','sourceThreadId':'source','approvedPhase':'execution'}
        d.authorize(self.record,evidence)
        self.assertEqual((self.record['revision'],self.record['status']),(2,'queued'))
        self.assertEqual(self.record['destination'],'destination')

    def test_null_project_is_inferred_and_wrong_projectless_is_blocked(self):
        project={'projectId':'p','projectKind':'local','path':str(self.root),'hostId':'local'}
        context=d.resolve_project([project],str(self.root));self.assertEqual(context['projectId'],'p')
        with self.assertRaises(ValueError):self.reserve(task='wrong',projects=[project])
        key=self.reserve(task='correct',context=context,projects=[project])
        with self.assertRaises(ValueError):d.attach(self.data[key],'d2','local',data=self.data)
        d.attach(self.data[key],'d2','local','p',self.data)

    def test_generated_source_preserves_project_and_uses_lineage_checkout(self):
        project={'projectId':'p','projectKind':'local','path':str(self.root),'hostId':'local'}
        outside=str(self.root.parent/'generated-chat')
        context=d.resolve_project([project],outside,'p');self.assertEqual(context['resolutionStatus'],'needs_checkout')
        lineage={'destination':'source','context':{'projectId':'p','sourceCwd':str(self.root)}}
        context=d.resolve_project([project],outside,'p',lineage=lineage,source='source')
        self.assertEqual(context['executionCwd'],str(self.root));self.assertEqual(context['projectId'],'p')

    def test_nested_runtime_root_preserves_execution_subdirectory(self):
        nested=self.root/'nested';nested.mkdir()
        project={'projectId':'p','projectKind':'local','path':str(self.root),'hostId':'local'}
        context=d.resolve_project([project],str(nested))
        key=self.reserve(task='nested',context=context,projects=[project]);record=self.data[key]
        d.attach(record,'destination','local','p',self.data);d.verify(record,self.runtime())
        self.assertEqual(record['status'],'profile_verified');self.assertEqual(record['context']['executionCwd'],str(nested))

    def test_existing_worktree_maps_via_git_common_dir(self):
        repo=self.root/'repo';repo.mkdir()
        def git(*args):subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
        git('init');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-m','fixture')
        tree=self.root/'existing-tree';git('worktree','add','--detach',str(tree))
        project={'projectId':'p','projectKind':'local','path':str(repo),'hostId':'local','isGitRepository':True}
        context=d.resolve_project([project],str(tree));self.assertEqual(context['projectId'],'p');self.assertEqual(context['executionCwd'],str(tree))

    def test_symlink_source_matches_canonical_project(self):
        real=self.root/'real';real.mkdir();alias=self.root/'alias';alias.symlink_to(real,target_is_directory=True)
        project={'projectId':'p','projectKind':'local','path':str(real),'hostId':'local'}
        context=d.resolve_project([project],str(alias))
        self.assertEqual(context['projectId'],'p');self.assertEqual(context['executionCwd'],str(real))

    def test_recommendations_and_explicit_supported_override(self):
        for kind,pair in {'bounded_read':('gpt-6-luna','low'),'routine':('gpt-6-luna','medium'),'complex':('gpt-6.1-sol','medium'),'critical':('gpt-6.1-sol','high')}.items():
            value=d.recommend(kind,self.catalog);self.assertEqual((value['model'],value['effort']),pair)
        self.assertEqual(d.recommend('bounded_read',self.catalog,'gpt-6-astra','high')['model'],'gpt-6-astra')
        with self.assertRaises(ValueError):d.recommend('routine',self.catalog,'unavailable','low')
        p=self.root/'caps.json';p.write_text(json.dumps({**self.catalog,'observedAt':time.time()-90000}))
        with self.assertRaises(ValueError):d.capabilities(p)

    def test_refresh_catalog_uses_exact_host_supported_efforts(self):
        text='gpt-6.1-sol (Workhorse; supported reasoning efforts: low, medium, high), gpt-6-luna (Routine; supported reasoning efforts: low, medium)'
        value=d.refresh_capabilities(text,'local')
        self.assertEqual(value['models']['gpt-6-luna'],['low','medium'])
        with self.assertRaises(ValueError):d.recommend('routine',value,'gpt-6-luna','high')
        with self.assertRaises(ValueError):d.refresh_capabilities('No supported metadata','local')

    def test_preflight_exact_project_arguments_and_retry_dedup(self):
        project={'projectId':'p','projectKind':'local','path':str(self.root),'hostId':'local'}
        request={'source':'s2','sourceCwd':str(self.root),'taskId':'new-task','requestId':'r','kind':'routine','authorization_phase':'execution'}
        result=d.preflight(self.data,request,[project],self.catalog,'Objective: fixture only. Acceptance: read no production data.')
        args=result['createThreadArguments'];self.assertEqual(args['target'],{'type':'project','projectId':'p','environment':{'type':'local'}})
        self.assertEqual((args['model'],args['thinking']),('gpt-6-luna','medium'))
        self.assertEqual(hashlib.sha256(args['prompt'].encode()).hexdigest(),self.data[result['key']]['promptSha256'])
        with self.assertRaises(ValueError):d.preflight(self.data,{**request,'requestId':'another-turn'},[project],self.catalog,'same task')

    def test_approval_only_preflight_has_no_create_arguments(self):
        request={'source':'s2','sourceCwd':str(self.root),'taskId':'approval','requestId':'r','kind':'routine','authorization_phase':'awaiting_approval'}
        result=d.preflight(self.data,request,[],self.catalog,'Await approval only.')
        self.assertIsNone(result['createThreadArguments']);self.assertEqual(result['status'],'awaiting_approval')

    def test_cli_atomic_reservation_and_lookup(self):
        ledger=self.root/'ledger.json';projects=self.root/'projects.json';req=self.root/'request.json';prompt=self.root/'prompt.txt';caps=self.root/'caps.json';output=self.root/'output.json'
        projects.write_text(json.dumps({'projects':[]}));caps.write_text(json.dumps(self.catalog));prompt.write_text('Fixture objective and acceptance.')
        request={'source':'s','sourceCwd':str(self.root),'taskId':'task','requestId':'r','kind':'bounded_read','authorization_phase':'audit_only'};req.write_text(json.dumps(request))
        cmd=[sys.executable,str(Path(__file__).with_name('dispatch.py')),'--ledger',str(ledger),'preflight','--request',str(req),'--projects',str(projects),'--prompt',str(prompt),'--capabilities',str(caps),'--output',str(output)]
        subprocess.run(cmd,check=True,capture_output=True);before=ledger.read_bytes()
        self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,2);self.assertEqual(before,ledger.read_bytes())
        key,record=d.lookup(json.loads(ledger.read_text()),'s','task');self.assertEqual(record['authorization_phase'],'audit_only')


if __name__=='__main__':unittest.main()
