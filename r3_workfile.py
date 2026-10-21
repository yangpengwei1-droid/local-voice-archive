"""Bound local workbook transaction. File is authority; durable intent recovers DB promotion."""
import datetime,fcntl,hashlib,json,os,sqlite3,uuid
from pathlib import Path
from r3_registry import R3Registry
from field_journal import FieldJournal

PROTECTED=Path(os.environ.get('LOCAL_VOICE_ARCHIVE_PROTECTED_SOURCE',str(Path(__file__).resolve().parent/'examples'/'demo.xlsx'))).expanduser()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def durable(path,raw):
    with Path(path).open('wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
def syncdir(path):
    fd=os.open(str(path),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
def savejson(path,value):
    tmp=Path(str(path)+'.tmp');durable(tmp,json.dumps(value,ensure_ascii=False,indent=2).encode());os.replace(tmp,path);syncdir(Path(path).parent)
def binding(work):return work.root/'workfile.json'
def check_target(path):
    p=Path(path).expanduser()
    if p.is_symlink() or p.suffix.lower()!='.xlsx' or not p.is_file():raise ValueError('请选择实际存在的本地xlsx工作副本（不能是符号链接）')
    p=p.resolve()
    if p==PROTECTED.resolve() or (PROTECTED.exists() and os.path.samefile(p,PROTECTED)):raise ValueError('受保护源文件不可绑定，请选择独立完整工作副本')
    return p

def recover(work):
    intent=work.root/'workfile-intent.json'
    if not intent.exists():return
    value=json.loads(intent.read_text())
    if value['state']=='done':return
    target=check_target(value['path']);actual=sha(target)
    if actual==value['new_sha']:
        # Staged DB contains exactly the operations rendered into this file.
        with sqlite3.connect(value['staged_db']) as a,sqlite3.connect(value['db']) as b:a.backup(b)
        config=json.loads(binding(work).read_text());config['sha']=actual;config['saved_at']=value['saved_at'];savejson(binding(work),config)
        if value.get('verification'):
            receipt=value['verification'];receipt.update(path=str(target),sha256=actual)
            ledger=work.root/'workfile-receipts.json';entries=json.loads(ledger.read_text()) if ledger.exists() else []
            if not any(e.get('transaction')==value.get('transaction') for e in entries):entries.append(dict(receipt,transaction=value.get('transaction')))
            savejson(ledger,entries)
        value['state']='done';savejson(intent,value)
    elif actual==value['old_sha']:
        value['state']='done';value['aborted']=True;savejson(intent,value)
    else:raise ValueError('保存恢复遇到外部修改，已阻断写入；保留事务、候选和备份供核对')

def bind(work,path,year):
    recover(work)
    p=check_target(path)
    if p.name=='导入基线.xlsx':raise ValueError('导入基线与旧会话快照不可作为工作文件')
    if type(year)is not int or not 2000<=year<=2099:raise ValueError('请明确选择归档年度（2000—2099）')
    # Each bind gets a new domain; historical 9999 operations are never replayed.
    staged=work.stage(p.read_bytes());key=uuid.uuid4().hex
    work.commit.__wrapped__(work,dict(version=work.status()['version'],token=staged['token'],test_only=True,confirmed=True,acknowledge_saved=True,workbook_id='local-'+key,domain='local-'+key,local_workfile=True,calendar_copy_test=False,year=year,first=1,last=99999999,width=0))
    if sha(p)!=staged['sha']:raise ValueError('绑定期间工作文件被外部编辑，请重新选择；没有保存业务内容')
    result=dict(path=str(p),sha=staged['sha'],generation=work.active['generation'],year=year,bound_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    savejson(binding(work),result);set_archive_year(work,year,False);return dict(kind='workfile_bound',**result,version=work.active['generation'],message='已绑定本地工作副本；后续按键确认将直接保存此文件')

def transact(work,path,data,invoke):
    if not binding(work).exists():return invoke(path,data)
    recover(work)
    config=json.loads(binding(work).read_text())
    if config['generation']!=work.active['generation']:raise ValueError('活动会话与工作文件不符，请重新绑定')
    target=check_target(config['path'])
    locknames=[target.parent/('~$'+target.name),target.parent/('.~lock.'+target.name+'#')]
    if any(p.exists() for p in locknames):raise ValueError('工作文件存在Office/LibreOffice占用标志，请关闭表格后重试')
    with target.open('rb') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('工作文件被占用')
        if sha(target)!=config['sha']:raise ValueError('工作文件已被外部编辑，禁止覆盖；请先核对并重新绑定')
        columns=[];row=data.get('row')
        if path in ('register','void'):columns=['F']
        elif path=='fields':columns=list(data.get('changes',{}))
        elif path=='undo':
            with work.registry.connect() as db:op=db.execute('SELECT old_json,new_json FROM f_ops WHERE request=?',(data.get('target_request'),)).fetchone()
            if op:
                old,new=map(json.loads,op);columns=[c for c in old if old[c]!=new[c]]
        elif path=='confirm_voice_preview':
            with work.registry.connect() as db:pending=db.execute('SELECT kind,payload FROM voice_pending WHERE id=?',(data.get('proposal_id'),)).fetchone()
            if pending:
                payload=json.loads(pending['payload']);columns=list(payload.get('changes',{})) if pending['kind']=='fields' else ['F']
                if payload.get('allocate_number'):columns.append('F')
        skipped=[]
        if path=='fields':
            state=work.current(row)['fields'];requested=data.get('changes',{})
            for c,v in requested.items():
                if data.get('expected',{}).get(c)!=state['current_fields'].get(c):raise ValueError('字段预期值已变化，请重新核对')
                if v==state['current_fields'].get(c) or (c=='X' and v==state['display_fields'].get(c)):skipped.append(c)
            if len(skipped)==len(requested):return dict(kind='no_changes',row=row,path=str(target),saved_at=config.get('saved_at'),unchanged_fields=skipped,message='实际值没有变化；未写入文件，不新增已保存标记')
            data=dict(data,changes={c:v for c,v in requested.items() if c not in skipped},expected={c:v for c,v in data['expected'].items() if c not in skipped})
            columns=[c for c in columns if c not in skipped]
        tx=uuid.uuid4().hex;folder=work.source.parent;stage=folder/('transaction-'+tx+'.sqlite')
        original=work.registry
        with original.connect() as a,sqlite3.connect(str(stage)) as b:a.backup(b)
        work.registry=R3Registry(stage);work.fields=FieldJournal(work.registry)
        try:
            result=invoke(path,data)
            if result.get('kind')=='confirmation_required':return result
            output=work.export();candidate=Path(output['path']);newsha=sha(candidate)
        finally:work.registry=original;work.fields=FieldJournal(original)
        if sha(target)!=config['sha']:raise ValueError('准备期间工作文件改变，未覆盖')
        stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
        if newsha==config['sha']:
            return dict(kind='no_changes',row=row,path=str(target),saved_at=config.get('saved_at'),unchanged_fields=columns+skipped,message='实际单元格值没有变化；未写入文件，也未生成新的已保存标记')
        previous_values=verification_for(work,row,columns,'',target.read_bytes())['fields']
        backup=folder/('工作文件备份-'+tx+'.xlsx');durable(backup,target.read_bytes())
        localtemp=target.parent/('.'+target.name+'.'+tx+'.tmp')
        durable(localtemp,candidate.read_bytes())
        stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
        verification=verification_for(work,row,columns,stamp,candidate.read_bytes());unchanged=[c for c in columns if verification['fields'][c]['value']==previous_values[c]['value']];verification['fields']={c:v for c,v in verification['fields'].items() if c not in unchanged};verification.update(path=str(target),sha256=newsha)
        intent=dict(transaction=tx,verification=verification,state='prepared',path=str(target),old_sha=config['sha'],new_sha=newsha,staged_db=str(stage),db=str(original.path),backup=str(backup),candidate=str(candidate),saved_at=stamp)
        savejson(work.root/'workfile-intent.json',intent)
        fault=getattr(work,'workfile_fault',lambda stage:None);fault('before_replace')
        if sha(target)!=config['sha']:localtemp.unlink(missing_ok=True);raise ValueError('保存前文件指纹冲突')
        os.replace(localtemp,target);syncdir(target.parent);fault('after_replace')
        # Reopen actual destination, parse and compare to already-verified candidate.
        from r3_xml import validate
        actual=target.read_bytes();validate(actual)
        if hashlib.sha256(actual).hexdigest()!=newsha:raise ValueError('实际文件回读失败，保留恢复事务，未报告成功')
        recover(work)
        result.update(write_action=path,unchanged_fields=unchanged+skipped,verification=verification,path=str(target),saved_at=stamp,sha256=newsha,backup=str(backup),message='已直接保存本地工作文件并重新读取核验成功：'+str(target)+'；时间 '+stamp+'；未同步线上')
        return result

def all_fields(work,row,fields,number):
    import zipfile,xml.etree.ElementTree as ET
    from app import NS
    from r3_xml import sheet_path,_header_value
    with zipfile.ZipFile(work.source) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(si.itertext()) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        tree=ET.fromstring(z.read(sheet_path(z,work.meta['sheet'])))
        rows=tree.findall('s:sheetData/s:row',NS)
        header=next((r for r in rows if any(c.attrib.get('r','').startswith('F') and '文件编号' in _header_value(c,strings) for c in r)),None)
        labels={__import__('re').sub(r'\d','',c.attrib['r']):_header_value(c,strings) for c in header} if header is not None else {}
        actual=next(r for r in rows if r.attrib['r']==str(row))
        values={__import__('re').sub(r'\d','',c.attrib['r']):_header_value(c,strings) for c in actual}
        values.update(fields['display_fields']);values['F']=number['number'] or '尚未编号'
        return [dict(column=c,label=labels.get(c,c),value=v) for c,v in values.items()]

def set_archive_year(work,year,invalidate=True):
    """Explicit user-selected receipt/archive year; never derived from contract."""
    if type(year)is not int or not 2000<=year<=2099:raise ValueError('归档年度须为2000—2099的明确年份')
    config=json.loads(binding(work).read_text())
    if config['generation']!=work.active['generation']:raise ValueError('工作文件与活动会话不符')
    with work.registry.connect() as db:
        row=db.execute('SELECT config FROM d_sessions WHERE id=?',(work.active['generation'],)).fetchone();rule=json.loads(row[0]);rule.update(year=year,min_digits=3,width=0)
        db.execute('UPDATE d_sessions SET config=? WHERE id=?',(json.dumps(rule),work.active['generation']))
        from collections import Counter
        for number,count in Counter(r['number'] for r in work.rows if r['number'].startswith(str(year))).items():
            if count>1:db.execute('INSERT OR IGNORE INTO d_conflicts VALUES(?,?,?)',(work.active['config']['workbook_id'],work.active['config']['domain'],number))
        if invalidate:db.execute('DELETE FROM voice_pending WHERE session=? AND completed=0',(work.active['generation'],))
    work.active['config'].update(year=year,min_digits=3,width=0);work.active['calendar_copy_test']=False
    with sqlite3.connect(work.catalog) as db:db.execute('UPDATE active SET value=? WHERE id=1',(json.dumps(work.active,ensure_ascii=False),))
    config['year']=year;savejson(binding(work),config)
    return dict(kind='archive_year_selected',year=year,workfile=config,message='已明确采用归档年度'+str(year)+'；不依据项目或签订年份自动改变，已有编号保持原样')

def inspect_actual(work,row):
    """Always reopen real bound XLSX. Never substitutes SQLite/cache values."""
    from app import read_xlsx
    from r3_xml import validate
    from r3_export import record_identity
    from field_journal import display_date
    if not binding(work).exists():raise ValueError('尚未绑定本地工作文件')
    config=json.loads(binding(work).read_text());p=check_target(config['path']);raw=p.read_bytes();rows,meta=validate(raw)
    actual=next((r for r in rows if r['row']==row),None)
    if actual is None or record_identity(actual)!=record_identity(work.record(row)):raise ValueError('实际文件行/合同身份已改变，禁止把缓存值当核对结果')
    values=dict(F=actual['number'],G=actual['procurement'],U=actual.get('returned',''),V=actual['closing'],X=actual['date']);shown=dict(values,X=display_date(values['X'],meta['date1904']))
    digest=hashlib.sha256(raw).hexdigest()
    return dict(path=str(p),sheet=meta['sheet'],row=row,identity=record_identity(actual),generation=work.active['generation'],sha256=digest,expected_sha=config['sha'],external_conflict=digest!=config['sha'],values=values,display_values=shown,cells={c:c+str(row) for c in values},verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),saved_at=config.get('saved_at'),year=config['year'],message='已重新打开实际本地文件核对；'+('检测到外部编辑，保存将阻断' if digest!=config['sha'] else '文件指纹一致'))

def verification_for(work,row,columns,stamp,raw):
    from app import read_xlsx
    from r3_export import record_identity
    from field_journal import display_date
    rows,meta=read_xlsx(raw);r=next(r for r in rows if r['row']==row)
    values=dict(F=r['number'],G=r['procurement'],U=r.get('returned',''),V=r['closing'],X=r['date'])
    return dict(row=row,identity=record_identity(r),generation=work.active['generation'],sheet=meta['sheet'],saved_at=stamp,fields={c:dict(cell=c+str(row),value=values[c],display_value=display_date(values[c],meta.get('date1904',work.meta['date1904'])) if c=='X' else values[c],saved_at=stamp) for c in columns})

def processing_year(now=None):
    """Business clock is always Beijing, including at the UTC year boundary."""
    from zoneinfo import ZoneInfo
    instant=now or datetime.datetime.now(datetime.timezone.utc)
    return instant.astimezone(ZoneInfo('Asia/Shanghai')).year

def registration_exception_year(work,row,identity,year):
    from r3_export import record_identity
    if identity!=record_identity(work.record(row)):raise ValueError('当前合同身份已变化，请重新选择')
    if type(year)is not int or not 2000<=year<=2099:raise ValueError('请填写2000—2099的完整年份')
    basis=registration_basis(work,row)
    if not basis['manual_year_required']:raise ValueError('A列登记日期有效，自动采用默认规则，无需手动年份')
    config=json.loads(binding(work).read_text());config.setdefault('registration_exceptions',{})[str(row)]={'identity':identity,'year':year}
    savejson(binding(work),config)
    return dict(kind='registration_exception_selected',message='已明确此合同的异常登记年份；仅保存编号依据，尚未分配或写入xlsx',workfile=config)

def registration_basis(work,row):
    """A/时间 confirmed by user on 2026-10-15; X/I never substitutes."""
    import zipfile,xml.etree.ElementTree as ET,re
    from app import NS
    from r3_xml import sheet_path,_header_value
    from field_journal import display_date
    with zipfile.ZipFile(work.source) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():strings=[''.join(si.itertext()) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        tree=ET.fromstring(z.read(sheet_path(z,work.meta['sheet'])))
        cell=tree.find(f'.//s:c[@r="A{row}"]',NS)
        value=_header_value(cell,strings) if cell is not None else ''
    valid=False;shown=''
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):shown=datetime.date.fromisoformat(value).isoformat()
        elif re.fullmatch(r'\d+(?:\.0+)?',value) and float(value)>0:shown=display_date(value,work.meta['date1904'])
        if shown:valid=2000<=datetime.date.fromisoformat(shown).year<=2099
    except (ValueError,OverflowError):pass
    archive=processing_year()
    if valid:
        year=int(shown[:4]);old=year<2024
        return dict(year=year if old else archive,registration_date=shown,registration_raw=value,registration_cell='A'+str(row),source='registration_date' if old else 'processing_year',manual_year_required=False,label=(f'A{row} 登记日期 {shown}（早于2024），采用登记年度 {year}' if old else f'A{row} 登记日期 {shown}（2024及以后），按北京时间当前办理年 {archive} 编号'))
    from r3_export import record_identity
    config=json.loads(binding(work).read_text());exception=config.get('registration_exceptions',{}).get(str(row),{})
    chosen=exception.get('year') if exception.get('identity')==record_identity(work.record(row)) else None
    return dict(year=chosen,registration_date=None,registration_raw=value,registration_cell='A'+str(row),source='manual_year',manual_year_required=True,manual_year_confirmed=chosen is not None,label=f'A{row} 登记日期为空或异常（原值：{value or "空"}）；'+(f'本合同已明确采用 {chosen} 年编号' if chosen else '请明确本合同编号年份后再分配，不从项目或签订日期推断'))
