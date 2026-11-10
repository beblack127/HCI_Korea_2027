"""Observed exploration progress and quote-grounded focus revisions.

Actions establish what was requested/reviewed, never comprehension, commitment or
experienced difficulty. A focus revision is separate from a whole-topic switch.
"""
import copy
import re
from .behavior import normalized, topic_steps

PROGRESS_LABELS = {"initial": "관심 탐색", "reviewing": "관점 검토 중", "narrowing": "범위 좁혀 검토 중", "revising": "관점 조정 중"}
OPERATIONS = {"add", "refine", "replace", "compare"}


def grounded_span(value, source, context=False):
    """Return literal source text, allowing only aligned particles/inflections.

    Never invent missing content words. A model's rearranged phrase can map back
    to an existing span; unmatched domain terms/conditions still reject it.
    """
    value=str(value or '').strip()
    if not value:
        return ''
    if value in source:
        return value
    def stem(word):
        word=word.casefold()
        for suffix in ('에서는','으로','에서','에게','하는','되는','이라는','에','의','을','를','이','가','은','는','한'):
            if word.endswith(suffix) and len(word)-len(suffix)>=2:
                return word[:-len(suffix)]
        return word
    parts=list(re.finditer(r'[a-zA-Z0-9_]+|[가-힣]+',source))
    tokens=[stem(m.group()) for m in parts]
    requested=[stem(x) for x in re.findall(r'[a-zA-Z0-9_]+|[가-힣]+',value)]
    ignored={'대한','관련','의','이','가','를','을','에','은','는','과','와'} | ({'상황'} if context else set())
    requested=[x for x in requested if x not in ignored or x in tokens]
    if not requested or any(x not in tokens for x in requested):
        return ''
    # Select the shortest interval containing every requested source token.
    wanted=set(requested);best=None
    for start in range(len(parts)):
        found=set()
        for end in range(start,len(parts)):
            found.add(tokens[end])
            if wanted<=found:
                span=source[parts[start].start():parts[end].end()]
                if best is None or len(span)<len(best):best=span
                break
    return best or ''


def correction_request(value):
    # A request to repair recommendations, independent of a difficulty judgement.
    return bool(re.search(r"(?:말고|아니라|아닌|대신|보다는)|(?:추천|후보|주제|설명).{0,40}(?:아니야|아닌가|달라|다르|잘못|빗나|맞지|맞는.*아니)|(?:내가|제가).{0,15}(?:궁금한|원하는|말한).{0,10}(?:건|것은)", value))


def _keys(value):
    return set(re.findall(r"[a-z]{2,}|[가-힣]{2,}", str(value).lower()))


def matches_focus(value, focus):
    if not focus:
        return False
    if normalized(focus) in normalized(value):
        return True
    # Particles may differ in an explicit correction, e.g. '사용자의 이해도'.
    words = {re.sub(r"(?:에서|으로|보다|만|를|을|의|가|이|은|는)$", "", w) for w in _keys(focus)} - {"사용자", "연구", "주제", "관심"}
    return bool(words) and words.issubset(_keys(value) | {re.sub(r"(?:를|을|의|가|이|은|는)$", "", w) for w in _keys(value)})


def _fallback_revision(content):
    if not correction_request(content) or not re.search(r"주제|후보|추천|내가|제가|관심|궁금한|원하는", content):
        return None
    # Only extract literal spans around an explicit contrast. No new topic text.
    match = re.search(r"(.+?)(?:말고|보다는|이\s*아니라|가\s*아니라)\s*(.+)", content)
    if match:
        before, after = match.groups()
    else:
        parts = re.split(r"[?？]", content, maxsplit=1)
        if len(parts) != 2 or not re.search(r"아니야|아닌가", parts[0]):
            return None
        before, after = parts
    before = re.sub(r"^.*?(?:주제들은?|후보들은?|추천은|추천이)\s*", "", before)
    before = re.split(r"만\s*반영|만\s*다루|아니야|아닌가", before, maxsplit=1)[0].strip(' ?,.')
    after = re.sub(r"(?:이|가)?\s*궁금해.*$|(?:에|으로)\s*관심.*$|(?:을|를)\s*(?:보고|알고)\s*싶.*$", "", after).strip(' ?,.')
    return {"operation": "replace", "previous_focus": before, "current_focus": after} if len(before)>=3 and len(after)>=5 else None


def validated_focus_update(raw, events, previous=None):
    latest = next((e for e in reversed(events) if e['type'] in {'user_input','topic_browse','topic_refine'}),None)
    if not latest or latest['type'] != 'user_input':
        return None
    content = latest['payload'].get('text','')
    value = raw.get('focus_update') or {}
    refs = [r for r in value.get('evidence_quotes',[]) if isinstance(r,dict)
            and r.get('event_id')==latest['id'] and len(r.get('quote',''))>=3 and r['quote'] in content]
    target = str(value.get('current_focus') or '').strip()
    if refs:
        target = grounded_span(target,refs[0]['quote'])
    source = str(value.get('previous_focus') or '').strip()
    operation = value.get('operation')
    contrast = _fallback_revision(content)
    if contrast:
        operation = 'replace'
        # An explicit literal contrast outranks a model's paraphrased/incorrect
        # role assignment, including one that accidentally retains the old focus.
        source = contrast['previous_focus']
        target = contrast['current_focus']
        refs = [{'event_id':latest['id'],'quote':content}]
    if not (operation in OPERATIONS and refs and len(target)>=3 and any(target in r['quote'] for r in refs)):
        value = contrast
        if not value:
            return None
        operation,source,target=value['operation'],value['previous_focus'],value['current_focus']
        refs=[{'event_id':latest['id'],'quote':content}]
    if operation=='replace' and not correction_request(content):
        return None
    # 'previous' must exist in observed language, not in the model's old hypothesis.
    prior_language=[e['payload'].get('text','') for e in events if e['type']=='user_input']
    prior_language += [e['payload'].get('title','') for e in events if e['type'] in {'topic_browse','topic_refine'}]
    if source and not any(matches_focus(v,source) for v in prior_language):
        source=''
    if operation=='replace' and not source:
        return None
    return {'operation':operation,'previous_focus':source[:150],'current_focus':target[:180],
            'evidence_quotes':refs,'evidence_ids':[latest['id']],'event_id':latest['id']}


def enrich_progress(state, events, previous=None):
    previous=previous or {}
    boundary=state.get('direction_context_start_event_id',0)
    scoped=[e for e in events if e['id']>=boundary]
    latest=next((e for e in reversed(scoped) if e['type'] in {'user_input','topic_browse','topic_refine'}),None)
    update=state.get('focus_update')
    history=copy.deepcopy(previous.get('focus_history',[])) if state.get('topic_relation')!='changed' else []
    if not history:
        # Continuing an older session must not re-adopt a previously corrected
        # perspective just because older saved states had no revision history.
        for index,event in enumerate(scoped):
            if event['type']=='user_input':
                reconstructed=validated_focus_update({},scoped[:index+1])
                if reconstructed:
                    history.append(reconstructed)
    if update and not any(x['event_id']==update['event_id'] for x in history):
        history.append(copy.deepcopy(update))
    state['focus_history']=history[-12:]
    excluded=[]
    for change in history:
        if change['operation']=='replace':
            excluded.append({'text':change['previous_focus'],'event_id':change['event_id']})
        # A later explicit positive adoption can reactivate an earlier focus.
        excluded=[x for x in excluded if not matches_focus(change['current_focus'],x['text'])]
    state['superseded_focuses']=excluded
    if state.get('research_problem') and any(matches_focus(state['research_problem'],x['text'])
            and max(state.get('problem_evidence_ids') or [0]) < x['event_id'] for x in excluded):
        state.update(research_problem='', problem_evidence=[], problem_evidence_ids=[])
        if state.get('direction_stage')=='forming':
            state['direction_stage']='unknown'
    active=[];superseded=[]
    for aspect in state.get('clarified_aspects',[]):
        update_id=update['event_id'] if update else 0
        negative_part=bool(update and update['operation']=='replace' and update_id in aspect.get('evidence_ids',[])
                           and matches_focus(aspect.get('text',''),update['previous_focus'])
                           and update['current_focus'] not in aspect.get('text',''))
        retired=any(matches_focus(aspect.get('text',''),x['text']) and max(aspect.get('evidence_ids') or [0])<=x['event_id'] for x in excluded)
        (superseded if retired or negative_part else active).append(aspect)
    state['clarified_aspects']=active
    state['superseded_aspects']=superseded
    steps=topic_steps(scoped)
    refinements=[s for s in steps if s['action']=='topic_refine']
    focus=state.get('current_direction','')
    source='estimate'
    if latest and latest['type'] in {'topic_browse','topic_refine'}:
        focus=latest['payload'].get('title','') or focus;source='choice'
    elif update:
        focus=update['current_focus'];source='utterance'
    elif latest and latest['type']=='user_input' and state.get('support_request')=='directions':
        focus=latest['payload'].get('text','') or focus;source='utterance'
    substantive_inputs=[e for e in scoped if e['type']=='user_input' and len(e['payload'].get('text',''))>=8
                        and not re.match(r'^(?:고마워|감사|알겠어|응|네|좋아|오케이)(?:[.!? ]|$)',e['payload'].get('text',''))]
    if update and update['operation']=='replace':
        status='revising'
        description=f"‘{update['previous_focus']}’보다 ‘{update['current_focus']}’에 관심을 두려는 정정이 드러났어요."
    elif refinements or (update and update['operation']=='refine'):
        status='narrowing'
        description=f"최근 ‘{focus}’ 안의 문제·범위를 좁혀 검토하고 있어요."
    elif steps or len({e['payload']['text'] for e in substantive_inputs})>=2 or (update and update['operation'] in {'add','compare'}):
        status='reviewing'
        description=f"최근 ‘{focus}’와 관련된 관점을 검토하고 있어요."
    else:
        status='initial';description='관심에서 살펴볼 연구 방향을 탐색하고 있어요.'
    evidence=list(dict.fromkeys(([latest['id']] if latest else [])+([refinements[-1]['id']] if refinements else [])))
    state['exploration_progress']={'status':status,'label':PROGRESS_LABELS[status],
        'focus':focus[:180],'source':source,'description':description,'evidence_ids':evidence,
        'problem_status':'expressed' if state.get('research_problem') else 'unconfirmed',
        'note':'관찰한 검토 흐름이며, 연구 문제의 확정이나 이해를 의미하지 않아요.'}
    return state
