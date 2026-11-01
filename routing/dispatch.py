"""Agent-operated routing ledger. Never creates chats or grants tool permissions."""
import argparse
import copy
import datetime
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time
import unicodedata

ROOT = Path(__file__).parent
TERMINAL = {'completed', 'cancelled'}
PHASES = {'audit_only', 'execution', 'awaiting_approval'}
ACTIVE = {'dispatching', 'queued', 'profile_verified', 'running', 'failed', 'blocked'}
STRONG = {'gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-astra', 'gpt-6-sol'}


def normalized(path):
    return unicodedata.normalize('NFC', str(Path(path).resolve()))


def within(path, root):
    return Path(normalized(path)).is_relative_to(Path(normalized(root)))


def common_dir(path):
    if not Path(path).is_dir():
        return None
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--git-common-dir'],
                            capture_output=True, text=True, timeout=5)
    return normalized(Path(path)/result.stdout.strip()) if result.returncode == 0 else None


def same_checkout(path, project):
    if within(path, project['path']):
        return True
    if not project.get('isGitRepository'):
        return False
    left = common_dir(path)
    return bool(left and left == common_dir(project['path']))


def resolve_project(projects, cwd, project_id=None, execution_cwd=None, lineage=None, source=None):
    """Resolve ownership separately from checkout; unknown checkout blocks reserve."""
    local = [p for p in projects if p.get('projectKind') == 'local']
    if project_id is not None:
        hits = [p for p in local if p['projectId'] == project_id]
    else:
        hits = [p for p in local if within(cwd, p['path'])]
        if hits:
            depth = max(len(Path(normalized(p['path'])).parts) for p in hits)
            hits = [p for p in hits if len(Path(normalized(p['path'])).parts) == depth]
        else:
            hits = [p for p in local if p.get('isGitRepository') and same_checkout(cwd, p)]
    if not hits and project_id is None:
        return {'target':'projectless', 'projectId':None, 'sourceCwd':normalized(cwd),
                'executionCwd':normalized(execution_cwd or cwd), 'hostId':'local', 'resolutionStatus':'ready'}
    if len(hits) != 1:
        raise ValueError('Source project missing or ambiguous; no projectless fallback')
    project = hits[0]
    actual = execution_cwd or (cwd if same_checkout(cwd, project) else None)
    origin = 'source_cwd'
    if actual is None and lineage:
        context = lineage.get('context', {})
        correction = lineage.get('projectCorrection', {})
        owner = context.get('projectId') or (correction.get('projectId') if correction.get('destinationMembershipVerified') else None)
        if lineage.get('destination') == source and owner == project['projectId']:
            actual = context.get('executionCwd') or context.get('sourceCwd')
            origin = 'verified_dispatch_lineage'
    if actual and not same_checkout(actual, project):
        raise ValueError('Execution checkout does not belong to the resolved project')
    return {'target':'local', 'projectId':project['projectId'], 'projectPath':project['path'],
            'sourceCwd':normalized(cwd), 'executionCwd':normalized(actual) if actual else None,
            'hostId':project.get('hostId', 'local'), 'resolutionStatus':'ready' if actual else 'needs_checkout',
            'checkoutEvidence':origin if not execution_cwd else 'explicit_verified_execution_path'}


def request_key(source, task_id):
    if not source or not task_id:
        raise ValueError('Source and stable task identity are required')
    return hashlib.sha256(json.dumps([source, task_id]).encode()).hexdigest()[:32]


def capabilities(path=None, now=None):
    value = json.loads(Path(path or ROOT/'capabilities.json').read_text())
    if not value.get('source') or not value.get('models') or not value.get('hostId'):
        raise ValueError('Verified host capability snapshot required')
    if not 0 <= (now or time.time())-value.get('observedAt', 0) <= value.get('maxAgeSeconds', 86400):
        raise ValueError('Capability snapshot is stale; refresh only the relevant host tool metadata')
    return value


def refresh_capabilities(metadata, host_id, profiles=None):
    pairs=re.findall(r'(gpt-[\w.-]+) \([^)]*?supported reasoning efforts: ([a-z, ]+)\)',metadata)
    models={model:[x.strip() for x in efforts.split(',')] for model,efforts in pairs}
    if not models or not host_id:raise ValueError('Current host tool model/effort metadata could not be parsed')
    value = {'schemaVersion':1,'hostId':host_id,'source':'current create_thread tool metadata',
            'sourceSha256':hashlib.sha256(metadata.encode()).hexdigest(),'observedAt':time.time(),
            'maxAgeSeconds':86400,'models':models}
    if profiles is not None:
        if not isinstance(profiles, dict) or set(profiles) != {'bounded_read','routine','complex','critical'}:
            raise ValueError('Profiles must define bounded_read, routine, complex and critical')
        value['routingProfiles'] = profiles
        for kind in profiles:
            recommend(kind, value)
    return value


def recommend(kind, catalog, model=None, effort=None):
    choices = {'bounded_read':('gpt-6-luna','low'), 'routine':('gpt-6-luna','medium'),
               'complex':('gpt-6.1-sol','medium'), 'critical':('gpt-6.1-sol','high')}
    if kind not in choices:
        raise ValueError('Task kind must be bounded_read, routine, complex or critical')
    suggested = catalog.get('routingProfiles', choices)[kind]
    if not isinstance(suggested, (list, tuple)) or len(suggested) != 2 or not all(isinstance(x,str) for x in suggested):
        raise ValueError('Each routing profile must be a model/effort pair')
    chosen = (model or suggested[0], effort or suggested[1])
    if chosen[0] not in catalog['models'] or chosen[1] not in catalog['models'][chosen[0]]:
        raise ValueError('Requested model/effort is not supported by the verified host snapshot')
    return {'recommendedModel':suggested[0], 'recommendedEffort':suggested[1],
            'model':chosen[0], 'effort':chosen[1], 'explicitOverride':bool(model or effort)}


def reserve(data, source, request_id, model, effort, context, prompt, projects,
            task_id=None, authorization_phase='execution', catalog=None, explicit_projectless=False):
    if not task_id or authorization_phase not in PHASES or not request_id:
        raise ValueError('Stable task ID, originating request and explicit authorization phase required')
    if catalog is None or catalog.get('hostId') != context.get('hostId', 'local'):
        raise ValueError('Matching host capability evidence required')
    if model not in catalog['models'] or effort not in catalog['models'][model]:
        raise ValueError('Unsupported actual routing profile')
    if not prompt.startswith('EXECUTION_MODE: ROUTED_WORK_V1\n'):
        raise ValueError('Execution marker required')
    if context.get('target') not in {'local','projectless'} or not context.get('sourceCwd'):
        raise ValueError('Resolved source context required')
    resolved = resolve_project(projects, context['sourceCwd'], context.get('projectId'), context.get('executionCwd'))
    if context['target'] == 'projectless' and resolved['target'] == 'local' and not explicit_projectless:
        raise ValueError('Null projectId is not projectless proof: source cwd matches a registered project')
    if context['target'] == 'local':
        if not context.get('executionCwd') or any(context.get(k) != resolved.get(k) for k in ('projectId','projectPath','hostId')):
            raise ValueError('Project/checkout context does not match current project evidence')
    elif context.get('projectId') is not None:
        raise ValueError('Projectless context cannot have a projectId')
    key = request_key(source, task_id)
    if any(r.get('source')==source and request_id in set(r.get('requestIds',[])+[r.get('requestId')]) for r in data.values()):
        raise ValueError('Originating request already mapped, including legacy history; reuse/reconcile it')
    if key in data or any(r.get('taskId') == task_id for r in data.values()):
        raise ValueError('Task already reserved; reuse its destination for retries/follow-ups')
    # A destination executing this task cannot become its router again.
    if any(r.get('destination') == source and r.get('taskId') == task_id for r in data.values()):
        raise ValueError('Same-task routing cycle rejected')
    data[key] = {'schemaVersion':2,'taskId':task_id,'revision':1,'source':source,'requestId':request_id,
                 'requestIds':[request_id], 'requestedModel':model,'requestedEffort':effort,
                 'context':copy.deepcopy(context),'promptSha256':hashlib.sha256(prompt.encode()).hexdigest(),
                 'authorization_phase':authorization_phase,'status':'awaiting_approval' if authorization_phase == 'awaiting_approval' else 'dispatching',
                 'destination':None,'createdAt':time.time(),'revisionStartedAt':time.time(),'events':[],
                 'projectResolution':{'basis':'current project snapshot + checkout evidence','explicitProjectlessOverride':explicit_projectless}}
    return key


def validate_destination_context(record, host, project_id):
    if host != record['context'].get('hostId','local'):
        raise ValueError('Destination host differs from source context')
    expected = record['context'].get('projectId') if record['context']['target'] == 'local' else None
    if project_id != expected:
        raise ValueError('Destination sidebar project differs from source context')


def mutable(record, allowed=None):
    if record['status'] in TERMINAL:
        raise ValueError('Terminal task cannot reopen; a new user-authorized task identity is required')
    if allowed is not None and record['status'] not in allowed:
        raise ValueError('Invalid task state transition from '+record['status'])


def attach(record, destination, host, project_id=None, data=None):
    mutable(record, {'dispatching'})
    if not destination or destination == record['source']:
        raise ValueError('Invalid destination identity')
    validate_destination_context(record, host, project_id)
    if data and any(not (r.get('taskId') == record.get('taskId') and r['source'] == record['source'])
                    and (r.get('destination') == destination or r.get('source') == destination) for r in data.values()):
        raise ValueError('Existing routed node cannot be reused as a fresh destination')
    record.update(destination=destination,hostId=host,status='queued',verifiedDestinationProjectId=project_id)


def block(record, evidence):
    mutable(record, ACTIVE)
    record.update(status='blocked',statusEvidence=evidence)
    record.pop('actualModel',None);record.pop('actualEffort',None)


def reconcile(record, destination, host, evidence, project_id=None, data=None):
    mutable(record, {'blocked'})
    if not evidence.strip() or record.get('destination') not in (None,destination):
        raise ValueError('Discovery evidence required; existing destination cannot be replaced')
    candidate=copy.deepcopy(record);candidate['status']='dispatching'
    attach(candidate,destination,host,project_id,data)
    candidate['reconciliationEvidence']=evidence
    record.clear();record.update(candidate)


def runtime_context(path, max_bytes=2097152):
    """Bounded head/tail read; incomplete final writes remain pending, not success."""
    path=Path(path).resolve()
    with path.open('rb') as stream:
        head=stream.readline(262144)
        stream.seek(0,os.SEEK_END);size=stream.tell();start=max(len(head),size-max_bytes)
        stream.seek(start);tail=stream.read(max_bytes)
    first=json.loads(head)
    if first.get('type') != 'session_meta':raise ValueError('Runtime session identity missing')
    if tail and not tail.endswith(b'\n'):
        return first['payload'],None,True
    lines=tail.splitlines()
    if start>len(head) and lines:lines=lines[1:]
    turn=None
    for line in lines:
        if not line.strip():continue
        item=json.loads(line)
        if item.get('type')=='turn_context':
            turn=dict(item['payload'])
            stamp=item.get('timestamp')
            turn['_eventAt']=datetime.datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp() if stamp else None
    if not turn:raise ValueError('Current runtime turn not available in bounded window')
    return first['payload'],turn,False


def verify(record, runtime_path):
    mutable(record, {'queued','profile_verified','running','failed'})
    try:
        session,turn,pending=runtime_context(runtime_path)
        if session.get('id')!=record.get('destination'):raise ValueError('Destination runtime identity differs')
        if pending:
            record.update(status='queued',verificationPending='Runtime final line is incomplete')
            record.pop('actualModel',None);record.pop('actualEffort',None)
            return
        if not turn.get('_eventAt') or turn['_eventAt']<record.get('revisionStartedAt',record.get('createdAt',0)):
            raise ValueError('Runtime turn predates the current user-request revision')
        effort=turn.get('effort',turn.get('collaboration_mode',{}).get('settings',{}).get('reasoning_effort'))
        if (turn.get('model'),effort)!=(record['requestedModel'],record['requestedEffort']):
            raise ValueError('Actual destination profile differs')
        ctx=record['context'];expected=ctx.get('executionCwd') or ctx.get('sourceCwd')
        if ctx['target']=='local':
            actual=normalized(turn.get('cwd',''));expected=normalized(expected)
            allowed={expected}
            if ctx.get('projectPath') and within(expected,ctx['projectPath']):allowed.add(normalized(ctx['projectPath']))
            if actual not in allowed:raise ValueError('Actual destination cwd is not a verified execution/project root')
        record.update(status='profile_verified',actualModel=turn['model'],actualEffort=effort,
                      actualCwd=turn.get('cwd'),runtimeEvidence=str(Path(runtime_path).resolve()),
                      runtimeTurnId=turn.get('turn_id'),profileObservedAt=time.time())
        record.pop('verificationPending',None)
    except (ValueError,OSError) as error:
        block(record,str(error))


def validate_snapshot(record, snapshot, now=None):
    if snapshot.get('threadId')!=record.get('destination') or not snapshot.get('turnId'):
        raise ValueError('Actual app thread/turn identity required')
    validate_destination_context(record,snapshot.get('hostId'),snapshot.get('projectId'))
    if not 0 <= (now or time.time())-snapshot.get('observedAt',0) <= 300:
        raise ValueError('Current app evidence is stale or future-dated')
    if record.get('runtimeTurnId') and snapshot['turnId']!=record['runtimeTurnId']:
        raise ValueError('App turn differs from verified runtime turn')


def observe(record, snapshot):
    mutable(record, {'profile_verified','running','queued','failed','stop_requested','cancel_requested'})
    validate_snapshot(record,snapshot)
    state=snapshot.get('turnStatus')
    if record['status'] in {'stop_requested','cancel_requested'}:
        if state in {'completed','failed','interrupted'} and snapshot.get('threadStatus') in {'idle','notLoaded'}:
            record['status']='paused' if record['status']=='stop_requested' else 'cancelled'
    elif state=='inProgress' and record.get('actualModel'):
        record.update(status='running',startedAt=record.get('startedAt',time.time()))
    elif state in {'failed','interrupted'} or snapshot.get('error'):
        record['status']='failed'
    elif state=='completed':
        record['status']='profile_verified' if record.get('actualModel') else 'queued'
    elif state in {'waitingForInput','waitingForApproval'}:
        record['status']='waiting_for_user'
    else:
        record['status']='queued'
    record['appEvidence']=copy.deepcopy(snapshot)


def finish(record, evidence):
    mutable(record, {'profile_verified','running'})
    if record.get('authorization_phase')=='awaiting_approval':raise ValueError('Implementation approval is pending')
    snapshot=evidence.get('app',{});validate_snapshot(record,snapshot)
    if snapshot.get('turnStatus')!='completed' or snapshot.get('error'):
        raise ValueError('Actual successful task completion required')
    review=evidence.get('review',{})
    if review.get('taskId')!=record.get('taskId') or review.get('revision')!=record.get('revision') or review.get('verdict')!='approved':
        raise ValueError('Current task/revision final review required')
    registration=record.get('reviewRequest',{})
    if not registration or any(review.get(k)!=registration.get(k) for k in
                              ('reviewId','taskId','revision','turnId','reviewerThreadId','artifactSha256','resultSha256')):
        raise ValueError('Review must match the registered current-revision reviewer and artifacts')
    session,turn,pending=runtime_context(review.get('runtimeFile',''))
    if pending or turn.get('turn_id')!=review.get('turnId') or turn.get('model') not in STRONG:
        raise ValueError('Actual stronger reviewer runtime required')
    if session.get('id') != review.get('reviewerThreadId'):
        raise ValueError('Reviewer identity does not match review evidence')
    if session['id'] != record['destination']:
        parent=session.get('source',{}).get('subagent',{}).get('thread_spawn',{}).get('parent_thread_id') if isinstance(session.get('source'),dict) else None
        if parent != record['destination']:raise ValueError('Child reviewer is not bound to this destination')
    hashes=review.get('artifactSha256',{})
    text_review=bool(review.get('resultSha256') and review['resultSha256']==snapshot.get('finalMessageSha256'))
    if not (hashes or text_review) or any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in hashes.items()):
        raise ValueError('Reviewed artifact manifest is missing or changed')
    record.update(status='completed',completedAt=time.time(),completionEvidence=copy.deepcopy(evidence))


def begin_review(record, evidence):
    mutable(record, {'profile_verified','running'})
    session,turn,pending=runtime_context(evidence.get('runtimeFile',''))
    if pending or turn.get('model') not in STRONG or turn.get('turn_id')!=evidence.get('turnId') or session.get('id')!=evidence.get('reviewerThreadId'):
        raise ValueError('Actual stronger reviewer turn evidence required')
    if not turn.get('_eventAt') or turn['_eventAt']<record.get('revisionStartedAt',record.get('createdAt',0)):
        raise ValueError('Reviewer execution predates the current request revision')
    if session['id']!=record['destination']:
        parent=session.get('source',{}).get('subagent',{}).get('thread_spawn',{}).get('parent_thread_id') if isinstance(session.get('source'),dict) else None
        if parent!=record['destination']:raise ValueError('Review child is not bound to destination')
    hashes=evidence.get('artifactSha256',{})
    if not (hashes or evidence.get('resultSha256')) or any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in hashes.items()):
        raise ValueError('Current review artifact manifest required')
    registration={**copy.deepcopy(evidence),'taskId':record['taskId'],'revision':record['revision']}
    registration['reviewId']=hashlib.sha256(json.dumps(registration,sort_keys=True).encode()).hexdigest()
    record['reviewRequest']=registration


def request_boundary(record, evidence=None):
    if evidence is None:return time.time()
    when=evidence.get('requestedAt',0)
    if (evidence.get('kind')!='actual_user_request' or evidence.get('sourceThreadId') not in {record['source'],record.get('destination')}
        or not record.get('revisionStartedAt',0)<=when<=time.time()+5):
        raise ValueError('Actual current user request timestamp/provenance required')
    return when


def related(record, request_id, intent, request_evidence=None):
    if intent=='status':return
    if intent in {'stop','cancel'}:
        mutable(record)
        record['status']='stop_requested' if intent=='stop' else 'cancel_requested'
    elif intent=='wait_approval':
        mutable(record);record.update(status='awaiting_approval',authorization_phase='awaiting_approval')
    elif intent=='followup':
        if record['status'] in TERMINAL or record['status'] in {'paused','waiting_for_user','awaiting_approval','stop_requested','cancel_requested'}:
            raise ValueError('Terminal/paused/approval task requires explicit user authorization; never another dispatch')
        boundary=request_boundary(record,request_evidence)
        record.update(revision=record.get('revision',1)+1,revisionStartedAt=boundary,status='queued' if record.get('destination') else 'dispatching')
        for key in ('actualModel','actualEffort','runtimeTurnId','appEvidence','completionEvidence','reviewRequest'):record.pop(key,None)
    else:raise ValueError('Unknown related request intent')
    record.setdefault('requestIds',[]).append(request_id)
    record.setdefault('events',[]).append({'requestId':request_id,'intent':intent,'at':time.time()})


def authorize(record, evidence):
    mutable(record, {'awaiting_approval','paused','waiting_for_user'})
    if (evidence.get('taskId')!=record.get('taskId') or evidence.get('revision')!=record.get('revision')
        or evidence.get('kind')!='explicit_user_approval' or not evidence.get('messageId')
        or evidence.get('sourceThreadId') not in {record['source'],record.get('destination')}
        or evidence.get('approvedPhase') not in {'audit_only','execution'}):
        raise ValueError('Explicit current task/revision user approval provenance required')
    boundary=request_boundary(record,evidence.get('requestEvidence'))
    record.update(revision=record.get('revision',1)+1,revisionStartedAt=boundary,authorization_phase=evidence['approvedPhase'],
                  status='queued' if record.get('destination') else 'dispatching',approvalEvidence=copy.deepcopy(evidence))
    for key in ('actualModel','actualEffort','runtimeTurnId','appEvidence','completionEvidence','reviewRequest'):record.pop(key,None)


def lookup(data, source, task_id):
    matches=[(k,r) for k,r in data.items() if r.get('taskId',k)==task_id and source in {r['source'],r.get('destination')}]
    if len(matches)!=1:raise ValueError('Task mapping missing or ambiguous; reconcile existing identity before dispatch')
    return matches[0]


def lookup_request(data, source, request_id):
    matches=[(k,r) for k,r in data.items() if r.get('source')==source and request_id in set(r.get('requestIds',[])+[r.get('requestId')])]
    if len(matches)!=1:raise ValueError('Original request mapping missing or ambiguous')
    return matches[0]


def preflight(data, request, projects, catalog, body):
    """One validated reservation and exact app-tool arguments, with no app mutation."""
    source=request['source'];project_id=request.get('projectId')
    parents=[r for r in data.values() if r.get('destination')==source]
    if len(parents)>1:raise ValueError('Ambiguous source task lineage')
    context=resolve_project(projects,request['sourceCwd'],project_id,request.get('executionCwd'),parents[0] if parents else None,source)
    if context.get('resolutionStatus')!='ready':raise ValueError('Project resolved; actual execution checkout still needs evidence')
    if request.get('explicitProjectless'):
        context.update(target='projectless',projectId=None)
    choice=recommend(request['kind'],catalog,request.get('model'),request.get('effort'))
    task_id=request['taskId'];key=request_key(source,task_id)
    meta={'source':source,'taskId':task_id,'requestId':request['requestId'],'revision':1,
          'profile':{'model':choice['model'],'effort':choice['effort']},'context':context,
          'authorization_phase':request['authorization_phase'],'dispatchKey':key}
    text=body.removeprefix('EXECUTION_MODE: ROUTED_WORK_V1\n')
    prompt='EXECUTION_MODE: ROUTED_WORK_V1\nROUTING_CONTEXT: '+json.dumps(meta,ensure_ascii=False)+'\n\n'+text
    reserve(data,source,request['requestId'],choice['model'],choice['effort'],context,prompt,projects,
            task_id,request['authorization_phase'],catalog,request.get('explicitProjectless',False))
    data[key]['routingDecision']={**choice,'kind':request['kind']}
    if request['authorization_phase']=='awaiting_approval':return {'key':key,'status':'awaiting_approval','createThreadArguments':None}
    target={'type':'project','projectId':context['projectId'],'environment':{'type':'local'}} if context['target']=='local' else {'type':'projectless'}
    return {'key':key,'status':'dispatching','createThreadArguments':{'model':choice['model'],'thinking':choice['effort'],
            'target':target,'title':request.get('title','Routed execution'),'prompt':prompt}}


def save(path,data):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as temp:
        json.dump(data,temp,ensure_ascii=False,indent=2);temp.write('\n');temp.flush();os.fsync(temp.fileno());name=temp.name
    os.replace(name,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger',type=Path,default=ROOT/'dispatches.json')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('key');p.add_argument('--source',required=True);p.add_argument('--task-id',required=True)
    p=sub.add_parser('resolve-project');p.add_argument('--projects',required=True);p.add_argument('--cwd',required=True);p.add_argument('--project-id');p.add_argument('--execution-cwd');p.add_argument('--source')
    p=sub.add_parser('recommend');p.add_argument('--kind',required=True);p.add_argument('--capabilities');p.add_argument('--model');p.add_argument('--effort')
    p=sub.add_parser('refresh-capabilities');p.add_argument('--metadata',required=True);p.add_argument('--host',required=True);p.add_argument('--profiles');p.add_argument('--output',default=str(ROOT/'capabilities.json'))
    p=sub.add_parser('reserve')
    for name in ('source','request-id','task-id','model','effort','context','prompt','projects'):p.add_argument('--'+name,required=True)
    p.add_argument('--capabilities');p.add_argument('--authorization-phase',choices=sorted(PHASES),required=True);p.add_argument('--explicit-projectless',action='store_true')
    p=sub.add_parser('lookup');p.add_argument('--source',required=True);group=p.add_mutually_exclusive_group(required=True);group.add_argument('--task-id');group.add_argument('--request-id')
    p=sub.add_parser('preflight')
    for name in ('request','projects','prompt','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--capabilities')
    for cmd in ('get','attach','reconcile','verify','observe','block','finish','review-start','related','authorize'):
        p=sub.add_parser(cmd);p.add_argument('--key',required=True)
        if cmd in {'attach','reconcile'}:
            p.add_argument('--destination',required=True);p.add_argument('--host',default='local');p.add_argument('--project-id')
        if cmd in {'block','reconcile','finish','review-start','authorize'}:p.add_argument('--evidence',required=True)
        if cmd=='verify':p.add_argument('--runtime',required=True)
        if cmd=='observe':p.add_argument('--snapshot',required=True)
        if cmd=='related':p.add_argument('--request-id',required=True);p.add_argument('--intent',required=True);p.add_argument('--request-evidence')
    sub.add_parser('metrics')
    args=parser.parse_args()
    def read(path):return json.loads(Path(path).read_text())
    try:
        if args.command=='key':print(request_key(args.source,args.task_id));return
        if args.command=='recommend':print(json.dumps(recommend(args.kind,capabilities(args.capabilities),args.model,args.effort)));return
        if args.command=='refresh-capabilities':
            value=refresh_capabilities(Path(args.metadata).read_text(),args.host,read(args.profiles) if args.profiles else None);save(Path(args.output),value)
            print(json.dumps({'hostId':value['hostId'],'models':list(value['models']),'observedAt':value['observedAt']}));return
        if args.command=='metrics':
            data=read(args.ledger) if args.ledger.exists() else {}
            counts={}
            for r in data.values():counts[r['status']]=counts.get(r['status'],0)+1
            latencies=[r['startedAt']-r['createdAt'] for r in data.values() if r.get('startedAt') and r.get('createdAt')]
            print(json.dumps({'records':len(data),'statuses':counts,'observedDispatchSeconds':latencies,'tokenUsage':None,'note':'No verified turn-scoped token evidence; no savings or price estimate.'}));return
        args.ledger.parent.mkdir(parents=True,exist_ok=True)
        with args.ledger.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            data=read(args.ledger) if args.ledger.exists() else {}
            cmd=args.command
            if cmd=='resolve-project':
                parents=[r for r in data.values() if r.get('destination')==args.source]
                if len(parents)>1:raise ValueError('Ambiguous dispatch lineage')
                print(json.dumps(resolve_project(read(args.projects)['projects'],args.cwd,args.project_id,args.execution_cwd,parents[0] if parents else None,args.source),ensure_ascii=False));return
            if cmd=='lookup':key,record=lookup(data,args.source,args.task_id) if args.task_id else lookup_request(data,args.source,args.request_id)
            elif cmd=='preflight':
                result=preflight(data,read(args.request),read(args.projects)['projects'],capabilities(args.capabilities),Path(args.prompt).read_text())
                output=Path(args.output)
                if not output.parent.is_dir():raise ValueError('Output directory must already exist')
                save(output,result);save(args.ledger,data)
                print(json.dumps({'key':result['key'],'status':result['status'],'requestFile':str(output.resolve())}));return
            elif cmd=='reserve':
                key=reserve(data,args.source,args.request_id,args.model,args.effort,read(args.context),Path(args.prompt).read_text(),read(args.projects)['projects'],args.task_id,args.authorization_phase,capabilities(args.capabilities),args.explicit_projectless)
            else:
                key=args.key;record=data[key]
                if cmd=='attach':attach(record,args.destination,args.host,args.project_id,data)
                elif cmd=='reconcile':reconcile(record,args.destination,args.host,args.evidence,args.project_id,data)
                elif cmd=='verify':verify(record,args.runtime)
                elif cmd=='observe':observe(record,read(args.snapshot))
                elif cmd=='block':block(record,args.evidence)
                elif cmd=='finish':finish(record,read(args.evidence))
                elif cmd=='review-start':begin_review(record,read(args.evidence))
                elif cmd=='related':related(record,args.request_id,args.intent,read(args.request_evidence) if args.request_evidence else None)
                elif cmd=='authorize':authorize(record,read(args.evidence))
            if cmd not in {'get','lookup'}:save(args.ledger,data)
            print(json.dumps({'key':key,**data[key]},ensure_ascii=False,indent=2))
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as error:parser.exit(2,str(error)+'\n')


if __name__=='__main__':main()
