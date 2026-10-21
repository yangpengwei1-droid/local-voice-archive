"""Persistent, contract-bound spoken previews and explicit confirmations."""
import json,re,uuid,datetime,time
from voice_router import field_proposal
from r3_export import record_identity
from r3_returned import proposal as returned_proposal

def handle(work,data):
    text=data['text'].strip();row=data.get('row')
    if re.search(r'查询|查找|查看|查一下|看看|是否|是不是|什么|怎么|多少|说过|说了|他说|她说|有人说|原话|提到|引用|写着|字样|假设|例如|比如|[“”\"「」『』]',text):
        return dict(kind='clarification',message='查询、引述或假设仅供核对，不生成修改')
    if re.fullmatch(r'(?:确认保存|确认登记|确认更正编号)[。！!]?$',text):
        if data.get('final') is not True:raise ValueError('只有最终语音可以确认')
        token=data.get('proposal_id')
        with work.registry.connect() as db:pending=db.execute('SELECT * FROM voice_pending WHERE id=?',(token,)).fetchone()
        if not pending:raise ValueError('没有待确认语音预览，请先明确修改内容')
        required={'fields':'确认保存','register':'确认登记','set_number':'确认更正编号'}[pending['kind']]
        if text.rstrip('。！!')!=required:raise ValueError('确认口令与预览动作不符，请说'+required)
        if pending['session']!=data['version'] or pending['row']!=row or pending['identity']!=data.get('selection_identity'):raise ValueError('语音确认与当前选定合同不符，未保存')
        if not data.get('_button_confirmation'):return dict(kind='confirmation_required',message='仅允许人工按键确认保存')
        payload=json.loads(pending['payload'])
        if not pending['completed'] and payload.get('expires_at',0)<time.time():raise ValueError('预览已过期，请重新口述并核对')
        args=work.args(row)
        basis=work.allocation_basis(row)
        if (pending['kind']=='register' or payload.get('allocate_number')) and payload.get('number_basis')!=basis:raise ValueError('登记年度或A列依据已变化，请重新生成预览')
        allocation_args={'allocation_year':basis['year']} if basis else {}
        if pending['kind'] in ('register','set_number') and work.active.get('calendar_copy_test') and work.active['config']['year']!=datetime.date.today().year:raise ValueError('年度已改变，请重新预览')
        if pending['kind']=='fields':
            if payload.get('allocate_number') and not pending['completed']:
                if work.record(row)['number'] or work.number_preview(row).get('number')!=payload['allocate_number']:raise ValueError('编号预览已改变，请重新生成整批预览')
                work.registry.register(pending['id']+'-number',**args,**allocation_args)
            result=work.fields.update(pending['id'],**args,expected=payload['expected'],changes=payload['changes'],closing_approved=payload['changes'].get('V')=='结案通过')
        elif pending['kind']=='register':
            if work.record(row)['number']:raise ValueError('原F已有号，不能普通语音登记覆盖')
            if not pending['completed'] and work.number_preview(row).get('number')!=payload['preview']:raise ValueError('编号预览已改变，请重新核对')
            result=work.registry.register(pending['id'],**args,**allocation_args)
        else:result=work.registry.set_number(pending['id'],**args,new_number=payload['new_number'],expected_number=payload['old_number'])
        with work.registry.connect() as db:db.execute('UPDATE voice_pending SET completed=1 WHERE id=?',(pending['id'],))
        return dict(kind='voice_saved',result=result,row=row,proposal_id=pending['id'],message='语音确认已本地持久保存并读回，尚未写出表格、未同步线上')
    if row is None:
        if re.search('文件编号|归档编号|项目编号|询价|比选|结案|签订日期|日期改|返回|返还|取走',text):return dict(kind='clarification',message='请先人工选定唯一合同，再口述字段修改；没有写入')
        return None
    if data.get('selection_identity')!=record_identity(work.record(row)):raise ValueError('语音修改需要当前选定合同身份')
    state=work.current(row);rules=work.meta['rules'][row]
    if '已比选' in rules.get('G',[]):
        text=re.sub(r'(^|[，,；;。])((?:请)?(?:写入|招采状态(?:改为|是)?|采购状态(?:改为|是)?)?)(?:已必选|你必选)(?=$|[，,；;。！!])',r'\1\2已比选',text)
    # ASR may omit clause punctuation. Insert boundaries only at explicit field
    # anchors; preserve U's 未签日期 / 日期未签字 and negative corrections.
    text=re.sub(r'(?<=[^，,；;。])(?<!合同)(?=合同(?:已经|已)(?:返回|返还|被取走)|(?:合同)?签订日期)','，',text)
    text=re.sub(r'(文件编号|归档编号|项目编号|结案报告)(?=(?:已经|已|一|以)(?:询价|比选|笔选))',r'\1，',text)
    allocate_with_fields=False
    clauses=re.split(r'[，,；;。]+',text)
    allocation=r'(?:给(?:这份|此)合同)?(?:分配|生成|登记)(?:新的?)?(?:文件编号|归档编号|项目编号)'
    if len(clauses)>1 and any(re.fullmatch(allocation,c.strip()) for c in clauses):
        allocate_with_fields=True;text='，'.join(c for c in clauses if not re.fullmatch(allocation,c.strip()))
        if state['number']['number']:return dict(kind='clarification',message='已有F编号，批量分配不能覆盖')
        if not (work.root/'workfile.json').exists():return dict(kind='clarification',message='编号和多字段整批保存需要先绑定本地工作文件')
        if not state['preview'].get('number'):return dict(kind='clarification',message=state['preview'].get('unavailable','无可用编号'))
    returned_changes={}
    direct_returned=returned_proposal(text,rules.get('U',[]),state['fields']['current_fields'].get('U','')) if re.search('返还|返回|取走',text) else None
    if direct_returned and direct_returned['kind']=='field_proposal':
        returned_changes=direct_returned['changes'];text=''
    elif direct_returned:
        remaining=[]
        for clause in re.split(r'[，,；;。]+',text):
            p=returned_proposal(clause,rules.get('U',[]),state['fields']['current_fields'].get('U',''))
            if p is None:remaining.append(clause)
            elif p['kind']!='field_proposal':return p
            elif returned_changes and returned_changes!=p['changes']:return dict(kind='clarification',message='返还状态不唯一')
            else:returned_changes.update(p['changes'])
        text='，'.join(remaining)
    # Narrow, observed file-ASR homophones generate a reviewable preview only.
    for spoken,value in [('一询价','已询价'),('以询价','已询价'),('一笔选','已比选'),('以笔选','已比选'),('接案通过','结案通过'),('接审核通过','结案审核通过'),('接案已经通过','结案已经通过')]:
        text=text.replace(spoken,value)
    restart=re.fullmatch(r'(.+?)[，, ]*不对[，, ]*改成(.+?)[。！!]?$',text)
    if restart:
        old,new=restart.groups()
        if old in rules['G'] and new in rules['G']:text='不是'+old+'，是'+new
        else:return dict(kind='clarification',message='半句改口需明确同一字段的前后值，请重新说明；没有写入')
    correction=re.fullmatch(r'不是(.+?)[，, ]*(?:而是|是)(.+?)[。！!]?$',text)
    date_correction=re.fullmatch(r'不对[，, ]*(?:改成|日期改为|签订日期改为)\d{1,2}月\d{1,2}日?',text)
    if re.search(r'不要|别|勿|是否|能否|吗|[？?]|不确定|未|没|不能|不可|不想|不改|不保存|不登记|不结案|不通过|不询价|不比选',text) or ('不' in text and not correction and not date_correction):return dict(kind='clarification',message='否定或疑问不保存，请重新明确修改内容')
    # Explicit procurement correction: negative old value is never applied.
    correction=re.fullmatch(r'不是(.+?)[，, ]*(?:而是|是)(.+?)[。！!]?$',text)
    parsed=text
    parsed=re.sub(r'(^|[，,；;。])(?:请)?(?:这份|这份材料|这份文件)(?:是|就是)结案报告(?=$|[，,；;。])',r'\1结案通过',parsed)
    for spoken,value in [('已经询价','已询价'),('询价完成','已询价'),('已经比选','已比选'),('比选完成','已比选'),('结案审核通过','结案通过'),('结案已经通过','结案通过')]:
        if spoken in parsed:parsed=parsed.replace(spoken,value)
    if re.search('询价|比选|招采',parsed) and not rules['G']:return dict(kind='clarification',message='该行G没有有效下拉，不生成修改')
    if '结案' in parsed and not rules['V']:return dict(kind='clarification',message='该行V没有有效下拉，不生成修改')
    if correction:
        corrected=re.fullmatch(r'不是(.+?)[，, ]*(?:而是|是)(.+?)[。！!]?$',parsed)
        old,new=corrected.groups()
        if old in rules['G'] and new in rules['G']:parsed=new
        else:return dict(kind='clarification',message='改口内容需明确目标字段和值')
    incomplete=re.fullmatch(r'(?:不对[，, ]*)?(?:改成|日期改为|签订日期改为)(\d{1,2})月(\d{1,2})日?',text)
    if incomplete:
        with work.registry.connect() as db:prior=db.execute("SELECT payload FROM voice_pending WHERE id=? AND session=? AND row=? AND identity=? AND completed=0 AND kind=?",(data.get('proposal_id'),data['version'],row,data['selection_identity'],'fields')).fetchone()
        previous=json.loads(prior['payload']).get('changes',{}).get('X') if prior else None
        if not previous:return dict(kind='clarification',message='年份不明，请说完整年月日')
        parsed=f'日期改为{previous[:4]}年{incomplete[1]}月{incomplete[2]}日'
    number=re.fullmatch(r'(?:请)?(?:把)?(?:文件编号|归档编号|项目编号)(?:改为|改成|更正为)([0-9]+|[0-9]{1,3}(?:[,，][0-9]{3})+)[。！!]?$',text)
    allocate=re.fullmatch(r'(?:给(?:这份|此)合同)?(?:分配|生成|登记)(?:新的?)?(?:文件编号|归档编号|项目编号)[。！!]?$',text)
    if number:
        kind='set_number';payload=dict(old_number=state['number']['number'],new_number=re.sub('[,，]','',number[1]))
        if not (work.active.get('calendar_copy_test') or (work.root/'workfile.json').exists()) or not re.fullmatch(str(work.active['config']['year'])+r'[1-9]\d*',payload['new_number']):return dict(kind='clarification',message='更正编号须在当年规则隔离模式中明确完整F号码；不修改E')
    elif allocate:
        if state['number']['number']:return dict(kind='clarification',message='F已有编号，普通分配不能覆盖；需明确更正完整文件编号')
        if not state['preview']['number']:return dict(kind='clarification',message=state['preview'].get('unavailable','当前无可用编号'))
        kind='register';payload=dict(old_number=None,preview=state['preview']['number'])
    else:
        proposal=field_proposal(parsed,rules)
        if returned_changes and proposal['kind']=='not_field_intent':proposal=dict(kind='field_proposal',changes={})
        if proposal['kind']=='field_proposal':proposal['changes'].update(returned_changes)
        if proposal['kind']=='not_field_intent':
            if '审核通过' in parsed or re.match(r'^(?:已经|采购状态|招采状态|已询|一询|以询|保存|确认)',parsed):return dict(kind='clarification',message='字段或确认口述不完整，未查询、未保存。请重说完整状态；保存需核对后按键。')
            return None
        if proposal['kind']!='field_proposal':return proposal
        # A state word mentioned inside conversation is not an edit command.
        state_values=sorted(set(rules.get('G',[])+rules.get('V',[])),key=len,reverse=True)
        for clause in filter(None,(v.strip() for v in re.split(r'[，,；;。]+',parsed))):
            clause=clause[:-1] if clause.endswith('了') else clause
            state_value=next((v for v in state_values if clause.endswith(v)),None)
            if state_value:
                prefix=clause[:-len(state_value)]
                if not re.fullmatch(r'(?:请)?(?:(?:把|将)?(?:这份|这个|此)?(?:合同)?(?:的)?(?:招采状态|招财状态|招采|采购状态|结案情况|结案状态)?(?:写入|登记|更新|改为|改成|设为|设置为|是|为)?)*',prefix):
                    return dict(kind='clarification',message='本句只是提及状态，修改意图不明确；原待保存内容保持')
            elif not re.fullmatch(r'(?:请)?(?:(?:合同)?(?:签订日期|日期)(?:改为|改成|是|为)?)?\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?',clause):
                return dict(kind='clarification',message='字段修改意图不完整；原待保存内容保持，请直接说明目标字段和状态')
        changes=proposal['changes']
        if 'X' in changes and work.meta['date1904'] and changes['X']<'1904-01-01':return dict(kind='clarification',message='日期早于1904系统基准，不生成修改')
        if 'X' in changes and not rules['X_supported']:return dict(kind='clarification',message=rules.get('X_plan',{}).get('reason','X样式不支持，禁止语音修改'))
        kind='fields';payload=dict(changes=changes,expected={c:state['fields']['current_fields'][c] for c in changes},expected_display={c:state['fields']['display_fields'][c] for c in changes})
    if allocate_with_fields:
        if kind!='fields':return dict(kind='clarification',message='整批编号需同时给出明确字段状态')
        payload['allocate_number']=state['preview']['number']
        payload['old_number']=None
    if kind=='register' or allocate_with_fields:payload['number_basis']=state['preview'].get('basis')
    previous_id=data.get('proposal_id')
    if previous_id:
        with work.registry.connect() as db:prior=db.execute('SELECT * FROM voice_pending WHERE id=? AND session=? AND row=? AND identity=? AND completed=0',(previous_id,data['version'],row,data['selection_identity'])).fetchone()
        if not prior:return dict(kind='clarification',message='原待保存预览已失效；界面内容保持，请重新核对，不自动覆盖')
        old=json.loads(prior['payload'])
        if old.get('expires_at',0)<time.time():return dict(kind='clarification',message='原待保存预览已过期，原内容保持；请明确取消后重新预览')
        if prior['kind']!='fields' or kind!='fields':return dict(kind='clarification',message='已有编号预览尚未处理，原预览保持，请先保存或明确取消')
        if any(state['fields']['current_fields'].get(c)!=v for c,v in old['expected'].items()):return dict(kind='clarification',message='原预览实际值已变化，内容保持供核对，未覆盖或写入')
        if old.get('allocate_number') and (old.get('number_basis')!=state['preview'].get('basis') or old['allocate_number']!=state['preview'].get('number')):return dict(kind='clarification',message='原编号依据已变化，原预览保持，请重新核对')
        payload={**old,**payload,'changes':{**old['changes'],**payload['changes']},'expected':{**old['expected'],**payload['expected']},'expected_display':{**old.get('expected_display',{}),**payload.get('expected_display',{})}}
    payload['expires_at']=time.time()+120
    token=uuid.uuid4().hex
    with work.registry.connect() as db:
        db.execute('DELETE FROM voice_pending WHERE session=? AND row=? AND completed=0',(data['version'],row))
        db.execute('INSERT INTO voice_pending VALUES(?,?,?,?,?,?,0)',(token,data['version'],row,data['selection_identity'],kind,json.dumps(payload,ensure_ascii=False)))
    return dict(kind='voice_preview',proposal_id=token,action=kind,payload=payload,row=row,raw_transcript=data.get('raw_text',data['text']),matching_text=text,message='请核对合同和旧值→新值，再按一次确认保存；只写预览列出的字段。无说话人识别。')
