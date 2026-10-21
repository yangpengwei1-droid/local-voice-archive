"""R3 isolated copy workbench. No WPS API, production numbering or upload."""
import hashlib,json,sqlite3,uuid,os,re,datetime,tempfile,fcntl,functools,time,logging,signal,sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
from collections import Counter
from app import search,norm,search_notice
from voice_router import terminal_command,field_proposal
from r3_xml import validate
from r3_export import export_copy,record_identity
from r3_registry import R3Registry,STORAGE
from field_journal import FieldJournal,display_date
from local_registry import RegistrationError
from r3_rules import same_fields,annual_preview
from r3_speech_text import matching_text

def exclusive_operation(fn):
    @functools.wraps(fn)
    def wrapped(self,*args,**kwargs):
        # Import copies a database before activation; serialize that entire
        # interval with edits across workers, not only the pointer UPDATE.
        with (self.root/'operation.lock').open('a') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:return fn(self,*args,**kwargs)
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
    return wrapped

def project_key(text):
    digits={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    def ordinal(match):
        value=match.group(1)
        if value.isdigit():return '第'+str(int(value))+'届'
        if '十' in value:
            a,b=value.split('十');number=(digits.get(a,1)*10)+digits.get(b,0)
        else:number=digits.get(value)
        return '第'+str(number)+'届' if number else match.group(0)
    return re.sub(r'第([一二三四五六七八九十]{1,3}|[0-9]{1,2})届',ordinal,norm(text))

class Workbench:
    def __init__(self,root=STORAGE):
        self.code_fingerprints={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('r3_app.py','r3_workfile.py','r3_service.py','r3_returned.py','r3_voice_actions.py','r3_registry.py','r3_speech_text.py','r3_retrieval.py','r3_evidence.py','r3_partial.py','VoiceAppR3.swift','web/r3.html')}
        self.root=Path(root).resolve()
        if self.root!=STORAGE.resolve() and not any(p.resolve() in self.root.parents for p in (Path('/tmp'),Path(tempfile.gettempdir()))):raise ValueError('仅允许固定隔离目录或独立临时测试目录')
        self.root.mkdir(parents=True,exist_ok=True)
        self.catalog=self.root/'catalog.sqlite'
        with sqlite3.connect(str(self.catalog)) as db:
            db.executescript('CREATE TABLE IF NOT EXISTS active(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT); CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS exports(generation TEXT,signature TEXT,path TEXT,sha TEXT);')
        from r3_workfile import recover
        recover(self)
        self.load()
        if self.active:
            with self.registry.connect() as db:db.execute('DELETE FROM voice_confirmation_gates')
    def load(self):
        with sqlite3.connect(str(self.catalog)) as db:r=db.execute('SELECT value FROM active WHERE id=1').fetchone()
        self.active=json.loads(r[0]) if r else None
        self.rows=[]
        if self.active:
            self.source=Path(self.active['source']);raw=self.source.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=self.active['sha']:raise ValueError('持久基线指纹不一致，停止服务')
            self.rows,self.meta=validate(raw)
            self.registry=R3Registry(self.active['db']);self.fields=FieldJournal(self.registry)
            bp=self.root/'workfile.json'
            if bp.exists() and json.loads(bp.read_text()).get('generation')==self.active['generation'] and not self.active['config'].get('min_digits'):
                from r3_workfile import set_archive_year
                set_archive_year(self,self.active['config']['year'],False)
    def status(self):
        with sqlite3.connect(str(self.catalog)) as db:row=db.execute('SELECT value FROM active WHERE id=1').fetchone()
        stored=json.loads(row[0]) if row else None
        if (stored or {}).get('generation')!=(self.active or {}).get('generation'):self.load()
        workfile_path=self.root/'workfile.json'
        workfile=json.loads(workfile_path.read_text()) if workfile_path.exists() else None
        from r3_workfile import processing_year
        return dict(processing_year=processing_year(),workfile=workfile,recognition_contexts=[p for p,n in Counter(r['project'] for r in self.rows if r['project']).most_common(60)],build='local-voice-archive-0.1.0-alpha',service_pid=os.getpid(),runtime_revision='public-alpha-1',code_fingerprints=self.code_fingerprints,version=self.active['generation'] if self.active else '',count=len(self.rows),active=self.active,mode='isolated_test_only',not_synced=True)
    def guard(self,version):
        self.status()
        if not self.active or version!=self.active['generation']:raise RegistrationError('会话已改变，请重新查询并选择')
        if hashlib.sha256(self.source.read_bytes()).hexdigest()!=self.active['sha']:raise RegistrationError('持久基线已改变，禁止按旧行号查询或写入')
    def stage(self,raw):
        if len(raw)>20_000_000:raise ValueError('上传副本超过20MB')
        rows,meta=validate(raw);token=uuid.uuid4().hex;folder=self.root/token;folder.mkdir()
        source=folder/'导入基线.xlsx'
        with source.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        duplicates=[n for n,c in Counter(r['number'] for r in rows if r['number']).items() if c>1]
        data=dict(token=token,source=str(source),sha=hashlib.sha256(raw).hexdigest(),count=len(rows),sheet=meta['sheet'],selection=meta['selection'],blocked_identity_count=len(meta['blocked_identity_rows']),historical_duplicate_numbers=duplicates,previous_version=self.status()['version'])
        with sqlite3.connect(str(self.catalog)) as db:db.execute('INSERT INTO imports VALUES(?,?)',(token,json.dumps(data,ensure_ascii=False)))
        return data
    @exclusive_operation
    def commit(self,data):
        if data.get('version','')!=self.status()['version']:raise ValueError('预览期间活动会话已改变')
        if data.get('test_only') is not True or data.get('confirmed') is not True:raise ValueError('需要明确隔离测试及会话确认')
        with sqlite3.connect(str(self.catalog)) as db:r=db.execute('SELECT value FROM imports WHERE id=?',(data['token'],)).fetchone()
        if not r:raise ValueError('未知导入预览')
        staged=json.loads(r[0])
        if staged['previous_version']!=data.get('version',''):raise ValueError('导入预览过期，请重选文件')
        raw=Path(staged['source']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=staged['sha']:raise ValueError('预览副本已改变')
        rows,meta=validate(raw)
        # Configuration stays explicit. This workbench permits TEST year 9999 only.
        config={k:data[k] for k in ['workbook_id','domain','year','first','last','width']}
        calendar_test=data.get('calendar_copy_test') is True
        if calendar_test:
            if config['year']!=datetime.date.today().year or config['width']!=0 or config['first']!=1:raise ValueError('当年规则隔离测试必须使用当前归档年度、不补零和起点1')
            profile=annual_preview(rows,config['year'])
            if profile['blocked']:raise ValueError('当年编号异常或重复，禁止分号')
        elif not data.get('local_workfile') and config['year']!=9999:raise ValueError('实际年度必须明确选择当年规则隔离测试；未启用正式模式')
        if not config['workbook_id'].strip() or not config['domain'].strip():raise ValueError('需填写工作簿身份与隔离编号域')
        generation=uuid.uuid4().hex;folder=Path(staged['source']).parent;dbpath=folder/('session-'+generation+'.sqlite')
        # Copy previous full database; activation pointer changes only after candidate succeeds.
        if self.active:
            with self.registry.connect() as a,sqlite3.connect(str(dbpath)) as b:a.backup(b)
        reg=R3Registry(dbpath);journal=FieldJournal(reg)
        blocked=set(meta['blocked_identity_rows']);unique=[r for r in rows if r['row'] not in blocked]
        # Full history remains in source and preview. New TEST domain tracks blank
        # rows only: duplicate production numbers never become allocation approval.
        selected=[r for r in unique if not r['number']]
        with reg.connect() as db:
            old=db.execute('SELECT config FROM d_sessions WHERE workbook=? AND domain=? ORDER BY rowid DESC LIMIT 1',(config['workbook_id'],config['domain'])).fetchone()
            expected={k:config[k] for k in ['year','first','last','width']}
            if old and json.loads(old['config'])!=expected:raise ValueError('已有测试域规则不符，禁止重设号段')
            if self.active:
                dirty=db.execute("SELECT 1 FROM d_business WHERE status IN ('registered','voided') LIMIT 1").fetchone() or db.execute('SELECT 1 FROM f_ops LIMIT 1').fetchone()
                if dirty and data.get('acknowledge_saved') is not True:raise ValueError('已有本地操作，需确认保留旧日志后切换；不自动合并旧字段')
                if dirty and meta['date1904']!=self.meta['date1904']:raise ValueError('已有本地操作且日期系统改变，禁止自动合并；请保持原1900/1904日期系统')
        # Reimport must reconcile saved work instead of silently resetting it.
        incoming={record_identity(r):r for r in unique}
        with reg.connect() as db:
            for saved in db.execute("SELECT * FROM d_business WHERE workbook=? AND domain=? AND status IN ('registered','voided')",(config['workbook_id'],config['domain'])):
                r=incoming.get(saved['business'])
                if not r or (r['number'] or None)!=saved['number']:raise ValueError('重导入与未回传本地编号不一致，旧会话保留；请先核对导出副本')
            seen=set()
            for saved in db.execute('SELECT b.business,c.values_json FROM f_base b JOIN f_current c ON c.session=b.session AND c.business=b.business JOIN d_sessions s ON s.id=b.session WHERE s.workbook=? AND s.domain=? ORDER BY s.rowid DESC',(config['workbook_id'],config['domain'])):
                if saved['business'] in seen:continue
                seen.add(saved['business'])
                r=incoming.get(saved['business'])
                if not r or not same_fields(json.loads(saved['values_json']),{'G':r['procurement'],'U':r.get('returned',''),'V':r['closing'],'X':r['date']},meta['date1904']):raise ValueError('重导入字段与本地保存值不一致，禁止清空或覆盖')
        reg.create_session(generation,baseline=staged['sha'],**config,confirmation_id='explicit-isolated-test',jointly_processing=True,no_other_numbering=True,records=[dict(business_id=record_identity(r),identity_fingerprint=record_identity(r),number='') for r in selected])
        with reg.connect() as db:
            historical=Counter(r['number'] for r in rows if r['number'])
            for number,count in historical.items():
                db.execute('INSERT OR IGNORE INTO d_used VALUES(?,?,?)',(config['workbook_id'],config['domain'],number))
                if count>1 and (data.get('local_workfile') or number.startswith(str(config['year']))):db.execute('INSERT OR IGNORE INTO d_conflicts VALUES(?,?,?)',(config['workbook_id'],config['domain'],number))
        active=dict(staged,generation=generation,db=str(dbpath),config=config,test_only=True,calendar_copy_test=calendar_test)
        # No fields initialized here: lazy per row avoids thousands of backup copies.
        with sqlite3.connect(str(self.catalog)) as db:
            db.execute('BEGIN IMMEDIATE');current=db.execute('SELECT value FROM active WHERE id=1').fetchone()
            current_version=json.loads(current[0])['generation'] if current else ''
            if current_version!=data.get('version',''):raise ValueError('激活冲突，旧会话保留')
            db.execute('INSERT OR REPLACE INTO active VALUES(1,?)',(json.dumps(active,ensure_ascii=False),))
        self.load();return self.status()
    def record(self,row):
        r=next((r for r in self.rows if r['row']==row),None)
        if not r:raise ValueError('记录不存在')
        return r
    def args(self,row):
        r=self.record(row)
        if row in self.meta['blocked_identity_rows']:raise ValueError('E/I/J/M身份重复，需消歧；本版禁止写入')
        return dict(session=self.active['generation'],baseline=self.active['sha'],business_id=record_identity(r),identity_fingerprint=record_identity(r))
    def ensure_fields(self,row):
        args=self.args(row);r=self.record(row);bid=args['business_id']
        # Historical-number rows are readable and field-editable without renumbering.
        with self.registry.connect() as db:present=db.execute('SELECT 1 FROM d_records WHERE session=? AND business=?',(args['session'],bid)).fetchone()
        if not present:
            # Existing F stays read-only; adding historical row to isolated field session.
            with self.registry.connect() as db:
                s=db.execute('SELECT * FROM d_sessions WHERE id=?',(args['session'],)).fetchone()
                current=db.execute('SELECT * FROM d_business WHERE workbook=? AND domain=? AND business=?',(s['workbook'],s['domain'],bid)).fetchone()
                if current and (current['identity']!=bid or current['number']!=r['number']):raise ValueError('历史号与本地登记冲突')
                db.execute('INSERT OR IGNORE INTO d_business VALUES(?,?,?,?,?,?)',(s['workbook'],s['domain'],bid,bid,r['number'],'historical'))
                db.execute('INSERT INTO d_records VALUES(?,?,?,?)',(args['session'],bid,bid,r['number']))
        with self.registry.connect() as db:exists=db.execute('SELECT originals FROM f_base WHERE session=? AND business=?',(args['session'],bid)).fetchone()
        if not exists:
            columns=['G','V']+(['U'] if self.meta.get('returned_supported') else [])
            rules={c:self.meta['rules'][row][c] or ['__UNAVAILABLE__'] for c in columns}
            original=dict(G=r['procurement'],V=r['closing'],X=r['date'])
            if self.meta.get('returned_supported'):original['U']=r.get('returned','')
            self.fields.initialize(session=args['session'],business_id=bid,identity_fingerprint=bid,originals=original,rules=rules)
        elif self.meta.get('returned_supported') and 'U' not in json.loads(exists['originals']):
            self.fields.add_returned_baseline(session=args['session'],business_id=bid,identity_fingerprint=bid,value=r.get('returned',''),choices=self.meta['rules'][row]['U'])
        # Rules may gain a program-standard fallback; never reset original/current values or old ops.
        with self.registry.connect() as db:
            rules={c:self.meta['rules'][row][c] or ['__UNAVAILABLE__'] for c in ['G','V']+(['U'] if self.meta.get('returned_supported') else [])}
            db.execute('UPDATE f_base SET rules=? WHERE session=? AND business=? AND identity=?',(json.dumps(rules),args['session'],bid,bid))
        return args
    def allocation_basis(self,row):
        """A is user-confirmed registration date; never infer from I or X."""
        if not (self.root/'workfile.json').exists():return None
        from r3_workfile import registration_basis
        return registration_basis(self,row)

    def number_preview(self,row):
        basis=self.allocation_basis(row)
        if basis and basis['year'] is None:return dict(number=None,reserved=False,basis=basis,unavailable='A列登记日期为空或异常，请先明确本合同编号年份')
        preview=self.registry.preview(self.active['generation'],year=basis['year'] if basis else None)
        if basis:
            preview.update(basis=basis)
        return preview

    def current(self,row):
        r=self.record(row)
        if row in self.meta['blocked_identity_rows']:return dict(record=r,blocked=True,message='身份重复，禁止写入')
        args=self.ensure_fields(row);fields=self.fields.current_state(session=args['session'],business_id=args['business_id'])
        fields['display_fields']=dict(fields['current_fields'],X=display_date(fields['current_fields']['X'],self.meta['date1904']))
        shown=fields['display_fields']['X']
        fields['date_warning']=bool(fields['current_fields']['X'] and (not shown or shown[:4]<'2010' or shown[:4]>'2030'))
        fields['rules']={c:self.meta['rules'][row][c] for c in ['G','U','V']}
        preview=self.number_preview(row)
        if self.active.get('calendar_copy_test') and self.active['config']['year']!=datetime.date.today().year:preview=dict(number=None,reserved=False,unavailable='归档年度已改变，请新建当前年度隔离会话')
        from r3_workfile import all_fields
        bp=self.root/'workfile.json';bound=json.loads(bp.read_text()) if bp.exists() else None
        return dict(workfile=bound,archive_year=self.active['config']['year'],all_fields=all_fields(self,row,fields,self.registry.current_state(**args)),returned_supported=self.meta.get('returned_supported',False),record=r,selection_identity=record_identity(r),number=self.registry.current_state(**args),fields=fields,capabilities=self.meta['rules'][row],not_synced=True,export_state=self.export_state(),preview=preview,annual_preview=annual_preview(self.rows,datetime.date.today().year))

    def operation_signature(self):
        with self.registry.connect() as db:
            requests=[list(r) for r in db.execute('SELECT request FROM d_ops WHERE session=? UNION SELECT request FROM f_ops WHERE session=? ORDER BY request',(self.active['generation'],self.active['generation']))]
        return hashlib.sha256(json.dumps(requests).encode()).hexdigest()

    def export_state(self):
        bp=self.root/'workfile.json'
        if bp.exists():
            bound=json.loads(bp.read_text());target=Path(bound['path'])
            if bound['generation']==self.active['generation']:
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=bound['sha']:return '实际工作文件缺失或已外部修改，须核对后保存'
                return '当前本地工作文件已核验；尚未同步线上'
        with sqlite3.connect(str(self.catalog)) as db:
            row=db.execute('SELECT path,sha FROM exports WHERE generation=? AND signature=? ORDER BY rowid DESC LIMIT 1',(self.active['generation'],self.operation_signature())).fetchone()
        if row and Path(row[0]).is_file() and hashlib.sha256(Path(row[0]).read_bytes()).hexdigest()==row[1]:return '当前本地状态已写出测试表格；尚未同步线上'
        return '当前本地状态尚未写出表格；尚未同步线上'
    def natural_contract_lookup(self,query,scope=None):
        # Only exact source titles/companies and explicit currency amounts.
        # A report's file type is not a contract field update.
        from decimal import Decimal,InvalidOperation
        import unicodedata
        query=unicodedata.normalize('NFKC',query)
        query=re.sub(r'(?<!\d)\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?',lambda m:m[0].replace(',',''),query)
        query=re.sub(r'(金额(?:是|为)?\s*)[¥￥]\s*',r'\1',query)
        amount=re.search(r'(?:金额(?:是|为)?\s*([0-9]+(?:\.[0-9]{1,2})?)|([0-9]+(?:\.[0-9]{1,2})?)\s*(?:元|金额))',query)
        content_query=re.sub(r'合同名称|合同名|结案报告|供应商|对应方|合作方','',query)
        titles=[v for v in {r['title'] for r in self.rows} if norm(v) and norm(v) in norm(content_query)]
        supplier_marker=re.search(r'(?:供应商|对应方|合作方)(?:是|为)?',query)
        suppliers=[v for v in {r['supplier'].strip() for r in self.rows} if norm(v) and norm(v) in norm(query)] if supplier_marker else []
        if not amount and not (titles and supplier_marker):return None
        titles=list({norm(v):v for v in titles}.values())
        distinct_titles=[t for t in titles if not any(norm(t)!=norm(other) and norm(t) in norm(other) for other in titles)]
        suppliers=[v for v in suppliers if not any(v!=other and v in other for other in suppliers)]
        if len(distinct_titles)>1 or len(suppliers)>1 or len(re.findall(r'金额(?:是|为)?\s*[0-9]+|[0-9]+\s*(?:元|金额)',query))>1:
            return dict(results=[],count=0,notice='出现多个合同、公司或金额，请一次核对一份合同。',query_clarification=True)
        if re.search(r'不是|不要|不能|不确定|别查|不对|改成|改为',query):
            return dict(results=[],count=0,notice='查询含否定或改口，请重说完整正向合同、公司及金额；没有选中合同。',query_clarification=True)
        base=[r for r in self.rows if scope is None or r['row'] in scope]
        if supplier_marker and not suppliers:
            return dict(results=[],count=0,notice='未找到完整对应方名称，请核对后重说；没有选中合同。',query_clarification=True)
        # A standalone amount/company can refine a scope. Unknown free text
        # plus an amount cannot silently collapse to a different contract.
        rest=query
        for value in sorted(titles+suppliers,key=len,reverse=True):rest=rest.replace(value,'')
        if amount:rest=rest.replace(amount[0],'')
        rest=re.sub(r'就像这个|这个|结案报告|合同名称|合同名|查找|查询项目|重新查找|重新查|供应商|对应方|合作方|人民币|元|(?:是|为)|[，,。；;：:\s]','',rest)
        if rest and not titles:return dict(results=[],count=0,notice='合同描述尚未精确匹配，请补充完整合同名称；没有选中合同。',query_clarification=True)
        def numeric(value):
            try:return Decimal(str(value).replace(',',''))
            except InvalidOperation:return None
        value=numeric(amount[1] or amount[2]) if amount else None
        title=max(distinct_titles,key=lambda v:len(norm(v))) if distinct_titles else None
        hits=[dict(r,requires_disambiguation=True,match='合同/公司/金额精确条件候选，仍需核对') for r in base if (not title or norm(r['title'])==norm(title)) and (not suppliers or norm(r['supplier']) in {norm(v) for v in suppliers}) and (not amount or numeric(r['amount'])==value)]
        return dict(results=hits,count=len(hits),unique_exact=False,query_filters=dict(title=title,suppliers=suppliers,amount=str(value) if amount else None),notice='仅召回精确条件候选；结案报告是文件背景，未修改任何状态。')

    def exact_source_entity(self,query,scope=None):
        # Recognize a complete source field before treating its internal words
        # as separate service/title/amount constraints. Read-only candidates.
        supplier=re.fullmatch(r'(?:供应商|对应方|合作方|公司)(?:是|为|[:：])?(.+)',query.strip())
        project=re.fullmatch(r'项目(?:名称)?(?:是|为|[:：])(.+)',query.strip())
        title=re.fullmatch(r'(?:合同名称|合同名)(?:是|为|[:：])?(.+)',query.strip())
        marked=supplier or project or title
        text=marked[1].strip() if marked else query.strip()
        if re.fullmatch(r'\d+',norm(text)) or re.match(r'关键词|文件编号|归档编号',text):return None
        fields=('supplier',) if supplier else ('project',) if project else ('title',) if title else ('project','title','supplier')
        q=project_key(text)
        matches=[dict(r,requires_disambiguation=True,match='源字段完整相符（'+ '、'.join(k for k in fields if project_key(r[k])==q) +'），仍需人工选择') for r in self.rows if (scope is None or r['row'] in scope) and any(project_key(r[k])==q for k in fields)]
        if not matches:return None
        return dict(results=matches,count=len(matches),unique_exact=False,retrieval_method='exact_source_field',notice='按完整源字段匹配，未将名称内部词另当筛选条件；主补合同分别核对。')

    def query_request(self,query,scope=None):
        started=time.perf_counter()
        original=query
        explicit=re.fullmatch(r'(?:请)?(?:重新查(?:找)?|查找|查询项目|查项目)[：:，,\s]*(.*)',query.strip())
        negative=bool(re.search(r'不要|别查|不能|不查询|不查找',query))
        if negative:
            return dict(results=[],count=0,raw_query=original,scope_source='unchanged_negative',scope_count=len(scope) if scope is not None else len(self.rows),query_elapsed_ms=0,notice='查询含否定，不切换范围、不选择合同。')
        if explicit:
            query=explicit[1].strip();scope=None
        if not query:
            return dict(results=[],count=0,raw_query=original,reset_query=bool(explicit),scope_source='explicit_new',scope_count=len(self.rows),query_elapsed_ms=0,notice='已请求新查询，请提供项目名称。')
        from r3_retrieval import retrieve
        retrieval_query=matching_text(query,(r[k] for r in self.rows for k in ('project','title','supplier')))
        approximate=retrieve(self.rows,retrieval_query,scope)
        entity=self.exact_source_entity(retrieval_query,scope)
        natural=self.natural_contract_lookup(query,scope) if entity is None else entity
        result=natural
        if entity is None and approximate is not None and (natural is None or natural.get('query_clarification') or not natural.get('count')):result=approximate
        if result is None:result=self.lookup(query,scope)
        result.update(raw_query=original,effective_query=query,reset_query=bool(explicit),scope_source='explicit_new' if explicit else 'global' if scope is None else 'continuation',scope_count=len(self.rows) if scope is None else len(scope))
        if scope is not None and not result['count'] and not result.get('related_count'):
            result['scope_exhausted']=True
            result['notice']='当前候选范围内没有匹配；本句未扩展到全表。下一句从全部合同重新检索。'
        if getattr(self,'active',None):
            with self.registry.connect() as db:
                saved={r['business']:r['number'] for r in db.execute('SELECT business,number FROM d_business WHERE workbook=? AND domain=?',(self.active['config']['workbook_id'],self.active['config']['domain']))}
            for key in ('results','related_results'):
                if key not in result:continue
                result[key]=[dict(r) for r in result[key]]
                for r in result[key]:
                    if record_identity(r) in saved:r['number']=saved[record_identity(r)] or ''
        result['query_elapsed_ms']=round((time.perf_counter()-started)*1000,2)
        return result

    def lookup(self,query,scope=None):
        base=[r for r in self.rows if scope is None or r['row'] in scope]
        original_query=query
        query=matching_text(query,(r[k] for r in self.rows for k in ('project','title','supplier')))
        file_number=re.fullmatch(r'(?:文件编号|归档编号)(?:是|为)?\s*(\d+)',query.strip())
        if file_number:query=file_number[1]
        keyword=re.fullmatch(r'关键词[：:，, ]*(.+)',query)
        if any(query==r[k] for r in base for k in ('project','title','supplier')):keyword=None
        if keyword:
            terms=[norm(v) for v in re.split(r'[，,；;\s]+',keyword[1]) if norm(v)]
            if len(terms)==1:
                # ASR often omits delimiters. Split only a known exact project
                # prefix, leaving the service fragment untouched. Never guess a name.
                projects=sorted({norm(r['project']) for r in base},key=len,reverse=True)
                prefix=next((p for p in projects if p and terms[0].startswith(p) and terms[0]!=p),None)
                if prefix:terms=[prefix,terms[0][len(prefix):]]
            if not terms or any(re.search(r'不是|不要|不能|不确定',v) for v in terms):return dict(results=[],count=0,raw_query=original_query,notice='关键词含否定或不明确内容，请分别重说正向关键词；没有选中合同。')
            hits=[dict(r,requires_disambiguation=True,match='多关键词候选，仍需逐份核对') for r in base if all(any(term in norm(r[k]) for k in ('project','title','supplier','contract','number')) for term in terms)]
            return dict(results=hits,count=len(hits),raw_query=original_query,keywords=terms,notice='关键词仅召回候选；主合同和补充协议仍需人工选择，未自动登记。')
        full=re.match(r'^项目(?:名称)?(?:是|市)(.+?)[，,。 ]*服务(?:内容)?(?:是|市)(.+?)[，,。 ]*(?:供应商是|供应商市|对应方式|对应方是)(.+)$',query.strip())
        if full:
            project,service,company=full.groups()
            project_result=self.lookup(project,None)
            if project_result.get('low_confidence'):
                project_result['raw_query']=original_query;return project_result
            service_result=self.lookup(service,[r['row'] for r in project_result['results']])
            result=self.lookup('供应商是'+company,[r['row'] for r in service_result['results']])
            result['raw_query']=original_query;result['explicit_project_correction']=True
            if any(r.get('approximate_service') for r in service_result['results']):
                for r in result['results']:r['approximate_service']=True
            return result
        query=re.sub(r'^对应方式','供应商是',query.strip())
        combined=re.match(r'^(?:服务是|服务市)(.+?)[，,。 ]*(?:供应商是|供应商市|对应方是|对应方式)(.+)$',query)
        if combined:
            service,company=combined.groups()
            service_result=self.lookup(service,scope)
            company_result=self.lookup('供应商是'+company,[r['row'] for r in service_result['results']])
            company_result['raw_query']=original_query
            company_result['combined_correction']=True
            if any(r.get('approximate_service') for r in service_result['results']):
                for r in company_result['results']:r['approximate_service']=True
            return company_result
        query=re.sub(r'^(?:服务(?:内容)?|合同内容|采购内容)(?:是|市|为|[:：])\s*','',query.strip())
        query=re.sub(r'^(?:对应方|合作方|公司)(?:是|市|为|[:：])\s*','供应商是',query)
        # Ordinal project names must not leak into contracts from other editions.
        ordinal=re.search(r'第(\d+)届',project_key(query))
        project_phrase=re.search(r'第\d+届(.+?(?:大会|会议))',project_key(query))
        supplier_hint=bool(re.match(r'^(供应商|不是|是)',query.strip()))
        if ordinal and project_phrase and not supplier_hint:
            edition=ordinal[1]
            base=[r for r in base if re.search(r'第'+re.escape(edition)+r'届',project_key(r['project']))]
            project_names=sorted({r['project'] for r in base})
            known_prefix=any(project_key(query).startswith(project_key(p)) for p in project_names)
            if not known_prefix:
                def core(value):return re.sub(r'中国|全国|国际|大会|会议', '', value)
                query_core=core(project_phrase[1])
                grams={query_core[i:i+2] for i in range(max(0,len(query_core)-1))}
                names=[]
                for name in project_names:
                    part=re.search(r'第\d+届(.+?(?:大会|会议))',project_key(name))
                    if part and (not grams or any(g in core(part[1]) for g in grams)):names.append(name)
                return dict(results=[],count=0,project_exact=False,low_confidence=True,raw_query=original_query,project_edition=int(edition),suggestions=[dict(query=name,message='同届项目的核心词近似，请核对全称后重新查询') for name in names],recognition_contexts=names[:60],notice='没有项目全名精确匹配；仅列同届且核心词相符的项目建议，请先核对。' if names else '未找到同届且核心词相符的项目；没有列出其他届合同，请核对原始转写或项目名称。')
        stated_years=set(re.findall(r'(?<!\d)(20\d{2})(?!\d)',query))
        if stated_years and not supplier_hint and not any(norm(query) in (norm(r['contract']),norm(r['number'])) for r in base):
            base=[r for r in base if any(y in r['project'] or y in r['title'] for y in stated_years)]
        q=norm(query);exact=[r for r in base if project_key(r['project'])==project_key(query)]
        title_hits=[r for r in base if q and q in norm(r['title'])]
        supplier=bool(re.match(r'^(供应商|不是|是)',query.strip()))
        result=exact or (title_hits if title_hits and not supplier else search(base,query))
        suggestions=[]
        if not exact and not supplier and q.endswith('项目') and len(q)>=6:
            from difflib import SequenceMatcher
            names=[name for name in sorted({r['project'] for r in base}) if SequenceMatcher(None,q,project_key(name)).ratio()>=.75]
            if names:
                result=[]
                suggestions=[dict(query=name,message='项目全名相近；保留源名，请先核对后重新查询') for name in names[:12]]
        # Explicit suggestions only: original ASR remains unchanged, no automatic
        # aliasing of company names and no write authority from a suggestion.
        corrections={'分为物料':'氛围物料','机票待定':'机票代订'}
        correction=corrections.get(q)
        if not result and not supplier and correction and any(norm(correction) in norm(r['title']) for r in base):
            suggestions.append(dict(query=correction,message='可能误识别，待人工核对；点击后仅重新查询'))
        # Restrict approximate service candidates to a previously supplied scope.
        # Keep the raw transcript; candidate recall does not mean exact matching.
        if not result and scope is not None and not supplier and len(q)>=2 and (len(q)>=3 or len({r['project'] for r in base})==1):
            from difflib import SequenceMatcher
            sound=str.maketrans({'分':'氛','为':'围','待':'代','定':'订','祭':'记'})
            approximate=[]
            for r in base:
                title=norm(r['title'])
                similarity=max((SequenceMatcher(None,q.translate(sound),title[i:i+len(q)].translate(sound)).ratio() for i in range(max(1,len(title)-len(q)+1))),default=0)
                if similarity>=(.5 if len(q)==2 else .6):approximate.append(dict(r,score=round(similarity,3),match='服务词近似，待核对；原转写未修改',approximate_service=True,requires_disambiguation=True))
            # Multiple approximate contracts stay separate; never auto-select.
            if approximate:result=sorted(approximate,key=lambda r:(-r['score'],r['row']))
        if not result and not supplier and not ordinal and len(q)>=4:
            from difflib import SequenceMatcher
            years=set(re.findall(r'20\d{2}',q))
            names=[]
            for name in sorted({r['project'] for r in base}):
                key=project_key(name)
                if years and set(re.findall(r'20\d{2}',key))!=years:continue
                if re.search(r'第\d+届',key) and re.search(r'第\d+届',q) is None:continue
                if SequenceMatcher(None,q,key).ratio()>=.70:names.append(name)
            suggestions.extend(dict(query=name,message='项目名称相近，保留源名；请核对年份和届数后重新查询') for name in names[:12])
        contexts=['已返还合同','已返还合同未签日期','已返还日期未签字','已被取走','已询价','已比选','已招标','结案通过','结案不通过','确认保存','确认登记','确认更正编号','停止监听']
        for r in (base if any(r.get('approximate_service') for r in result) else result or base):
            for value in (r['project'],r['title'],r['supplier']):
                if value and len(value)<=60 and value not in contexts:contexts.append(value)
                if len(contexts)>=100:break
            if len(contexts)>=100:break
        suggestions=list({v['query']:v for v in suggestions}.values())[:12]
        unique_exact=len(result)==1 and result[0]['row'] not in self.meta.get('blocked_identity_rows',[]) and not result[0].get('approximate_service') and (bool(exact) or q in (norm(result[0]['title']),norm(result[0]['contract'])) or result[0].get('supplier_equal',False))
        return dict(unique_exact=bool(unique_exact),results=result,count=len(result),project_exact=bool(exact),suggestions=suggestions,recognition_contexts=contexts,raw_query=original_query,notice=('服务词近似候选，请核对；不是精确匹配。' if any(r.get('approximate_service') for r in result) else None if exact or (title_hits and not supplier) else search_notice(query,result)))
    @exclusive_operation
    def action(self,path,d):
        if path=='set_registration_exception_year':
            self.guard(d.get('version'))
            from r3_workfile import registration_exception_year
            return registration_exception_year(self,d['row'],d.get('selection_identity'),d['year'])
        if path=='set_archive_year':
            self.guard(d.get('version'))
            from r3_workfile import set_archive_year
            return set_archive_year(self,d['year'])
        if path=='inspect_workfile':
            self.guard(d.get('version'))
            from r3_workfile import inspect_actual
            return inspect_actual(self,d['row'])
        if path=='bind_workfile':
            if d.get('version','')!=self.status()['version']:raise RegistrationError('会话已改变，请重新选择工作文件')
            from r3_workfile import bind
            return bind(self,d['path'],d.get('year'))
        if path=='fields' and 'X' in d.get('changes',{}):
            from field_journal import normalize_date_input
            d=dict(d,changes=dict(d['changes'],X=normalize_date_input(d['changes']['X'])))
        mutations={'register','void','fields','undo','confirm_voice_preview'}
        if path in mutations:
            if d.get('require_workfile') and not (self.root/'workfile.json').exists():raise ValueError('请先选择并绑定本地工作xlsx；没有写入')
            from r3_workfile import transact
            return transact(self,path,d,self._action)
        return self._action(path,d)
    def _action(self,path,d):
        self.guard(d.get('version'))
        if path=='cancel_voice_confirmation':
            token=d.get('proposal_id')
            with self.registry.connect() as db:
                if token:
                    db.execute('DELETE FROM voice_confirmation_gates WHERE proposal IN (SELECT id FROM voice_pending WHERE id=? AND session=?)',(token,d['version']))
                    if d.get('discard_preview'):db.execute('DELETE FROM voice_pending WHERE id=? AND session=? AND completed=0',(token,d['version']))
                else:
                    db.execute('DELETE FROM voice_confirmation_gates WHERE proposal IN (SELECT id FROM voice_pending WHERE session=?)',(d['version'],))
                    if d.get('discard_preview'):db.execute('DELETE FROM voice_pending WHERE session=? AND completed=0',(d['version'],))
            return dict(kind='voice_confirmation_closed')
        if path=='save_site_log':
            entries=d.get('entries')
            if not isinstance(entries,list) or len(entries)>500 or any(not isinstance(v,dict) for v in entries):raise ValueError('Invalid local test log')
            folder=self.root/'onsite_logs';folder.mkdir(exist_ok=True);path=folder/('onsite_'+uuid.uuid4().hex+'.json')
            path.write_text(json.dumps(dict(version=d['version'],build=self.status()['build'],audio_saved=False,entries=entries,note='manual_end is a user click proxy, not actual speech end'),ensure_ascii=False,indent=2))
            return dict(path=str(path),audio_saved=False)
        if path in ('arm_voice_confirmation','confirm_voice_preview'):
            row=d.get('row');identity=d.get('selection_identity');token=d.get('proposal_id')
            if row is None or identity!=record_identity(self.record(row)):raise ValueError('请先核对唯一选中合同')
            with self.registry.connect() as db:pending=db.execute('SELECT * FROM voice_pending WHERE id=?',(token,)).fetchone()
            if not pending or pending['session']!=d['version'] or pending['row']!=row or pending['identity']!=identity:raise ValueError('预览已失效或合同不符，请重新口述并核对')
            if path=='arm_voice_confirmation':
                raise ValueError('仅允许人工按键确认保存，口头确认已停用')
                if pending['completed']:raise ValueError('此预览已保存，无需再次授权')
                until=time.time()+8
                with self.registry.connect() as db:
                    db.execute('DELETE FROM voice_confirmation_gates WHERE expires<?',(time.time(),))
                    db.execute('INSERT OR REPLACE INTO voice_confirmation_gates VALUES(?,?)',(token,until))
                return dict(kind='voice_confirmation_armed',expires=until,message='本次预览8秒内允许一次口头确认；没有说话人识别，旁人仍可能被识别，办公室优先按键保存。')
            from r3_voice_actions import handle as voice_action
            words={'fields':'确认保存','register':'确认登记','set_number':'确认更正编号'}[pending['kind']]
            return voice_action(self,dict(d,text=words,final=True,_button_confirmation=True))
        if path=='voice':
            if d.get('final') is False and d.get('utterance_ended') is True:
                # Ended ASR hypotheses may propose fields, never query, allocate or save.
                text=d.get('text','').strip()
                if d.get('row') is None:
                    from r3_voice_actions import handle as voice_action
                    field=voice_action(self,d)
                    if field:return field
                    if re.search(r'不要|不查|停止|确认保存|分配.*编号',text) or len(norm(text))<2:
                        return dict(kind='clarification',message='本句含否定、控制词或不完整内容，请重说；未查询或写入')
                    return dict(kind='query',asr_unconfirmed=True,message='本句是待核对转写，仅查询候选，不自动选合同或保存')
                from r3_returned import value as returned_value
                returned_target=returned_value(text)
                if not returned_target and not re.fullmatch(r'(?:请)?(?:写入)?(?:已询价|已经询价|已比选|已经比选|已招标|合同(?:已|已经)(?:返还|返回)|(?:已|已经)(?:返还|返回)|结案通过|这份是结案报告|(?:(?:合同)?签订日期(?:改为|是)?)?[0-9零〇一二三四五六七八九十年月日/-]+)(?:[，,；;。]*(?:合同(?:已|已经)(?:返还|返回)|(?:已|已经)(?:返还|返回)|结案通过|已询价|已比选|(?:(?:合同)?签订日期(?:改为|是)?)?[0-9零〇一二三四五六七八九十年月日/-]+))*[。！!]?',text):
                    return dict(kind='clarification',message='本句只有未最终确认转写，请校正文字后生成预览；没有写入')
                from r3_voice_actions import handle as voice_action
                proposal=voice_action(self,d)
                if proposal and proposal.get('kind')=='voice_preview' and proposal.get('action')=='fields':
                    proposal['asr_unconfirmed']=True
                    proposal['message']='识别器未返回最终转写：请逐字核对上述待保存字段，点击确认才写入。'
                    return proposal
                return proposal or dict(kind='clarification',message='没有明确字段状态，请校正文字后生成预览')
            if d.get('final') is False:
                if re.fullmatch(r'(?:确认保存|确认登记|确认更正编号)[。！!]?$',d['text'].strip()):raise ValueError('只有最终语音可以确认；保存仍需按键')
                return dict(kind='clarification',message='尚未取得最终转写，请重说或用文字校正；没有生成修改')
            d={k:v for k,v in d.items() if k!='_button_confirmation'}
            d=dict(d,raw_text=d['text'],text=matching_text(d['text'],(r[k] for r in self.rows for k in ('project','title','supplier'))))
            clean=norm(d['text'])
            def invalidate_voice():
                if d.get('row') is not None:return # unrelated speech never cancels a selected contract draft
                with self.registry.connect() as db:
                    db.execute('DELETE FROM voice_confirmation_gates WHERE proposal IN (SELECT id FROM voice_pending WHERE session=?)',(d['version'],))
                    db.execute('DELETE FROM voice_pending WHERE session=? AND completed=0',(d['version'],))
            if any(word in clean for word in ('下一份','下一个','下一条','夏衣粉')):
                return dict(kind='manual_next_required',message='换下一份请点击“下一个”按钮；当前合同、范围和未保存预览保持不变。')
            new_query=re.fullmatch(r'(?:请)?(?:重新查(?:找)?|查找|查询项目|查项目)[：:，,\s]*(.*)',d['text'].strip())
            if new_query and not re.search(r'不要|别查|不能|不查询|不查找',d['text']):
                return dict(kind='new_query',query=new_query[1].strip(),raw_text=d['raw_text'])
            if not clean:
                invalidate_voice();return dict(kind='clarification',message='仅识别到停顿或填充词，没有操作；请说完整查询或修改内容。')
            command=terminal_command(d['text'],[r['project'] for r in self.rows])
            if command:
                if command=='next':return dict(kind='manual_next_required',message='换下一份请点击“下一个”按钮；未切换，预览保留。')
                if command in ('stop','clarify'):invalidate_voice()
                return dict(kind=command)
            number_query=re.fullmatch(r'(?:文件编号|归档编号)(?:是|为)?\s*(\d+)',d['text'].strip())
            if d.get('row') is None and number_query:return dict(kind='new_query',query=number_query[1],raw_text=d['raw_text'])
            if d.get('row') is None and self.natural_contract_lookup(d['text'],None) is not None:
                invalidate_voice();return dict(kind='query',file_context_only=True)
            from r3_voice_actions import handle as voice_action
            proposal=voice_action(self,d)
            if proposal is not None:
                return proposal
            if d.get('row') is not None:
                proposal=field_proposal(d['text'],self.meta['rules'][d['row']])
                if proposal['kind']!='not_field_intent':return proposal
                invalidate_voice();return dict(kind='clarification',message='当前合同保持选中；没有明确的字段修改，未查询、未保存。要换合同请点击“下一个”按钮。')
            invalidate_voice()
            return dict(kind='query')
        if path=='search':return self.query_request(d['query'],d.get('scope'))
        if path=='current':return self.current(d['row'])
        if path=='export':return self.export()
        row=d['row'];r=self.record(row)
        if d.get('selection_identity')!=record_identity(r):raise ValueError('确认合同身份缺失或与选中行不一致，禁止写入；请刷新并重新选择')
        args=self.ensure_fields(row);key=d['request_id']
        if path=='register' and d.get('expected_number') is not None and d['expected_number']!=self.number_preview(row).get('number'):raise ValueError('下一号已变化，请重新核对预览')
        if path=='register' and not (self.root/'workfile.json').exists() and d.get('archive_year') is not None and d['archive_year']!=self.active['config']['year']:raise ValueError('归档年度已变化，请重新核对')
        if path=='register':
            if self.active.get('calendar_copy_test') and self.active['config']['year']!=datetime.date.today().year:raise ValueError('归档年度已改变，禁止继续按旧年度分号；请新建当前年度隔离会话')
            if r['number']:raise ValueError('已有F编号保持原样，无需分配')
            basis=self.allocation_basis(row)
            if basis and not self.number_preview(row).get('number'):raise ValueError(self.number_preview(row).get('unavailable','没有可用编号'))
            if d.get('number_basis') is not None and d['number_basis']!=basis:raise ValueError('登记年度或A列依据已变化，请重新核对')
            return self.registry.register(key,**args,**({'allocation_year':basis['year']} if basis else {}))
        if path=='void':return self.registry.amend(key,**args,target_request=d['target_request'],action='void',reason='用户明确撤销隔离测试登记')
        if path=='fields':
            if d.get('voice_proposal_id'):
                with self.registry.connect() as db:p=db.execute('SELECT * FROM voice_pending WHERE id=?',(d['voice_proposal_id'],)).fetchone()
                if not p or p['completed'] or p['session']!=d['version'] or p['row']!=row or p['identity']!=d['selection_identity']:raise ValueError('语音预览失效，请重新口述')
                payload=json.loads(p['payload'])
                if payload.get('expires_at',0)<time.time():raise ValueError('语音预览已过期，请重新口述')
                if payload.get('allocate_number') or any(payload.get('changes',{}).get(c)!=v or payload.get('expected',{}).get(c)!=d['expected'].get(c) for c,v in d['changes'].items()):raise ValueError('语音分项预览不符，请重新核对')
            for col in d['changes']:
                if col in ('G','U','V') and not self.meta['rules'][row][col]:raise ValueError(col+'缺少有效下拉规则，禁止写入')
                if col=='X' and not self.meta['rules'][row]['X_supported']:raise ValueError(self.meta['rules'][row].get('X_plan',{}).get('reason','X原日期样式尚未支持'))
                if col=='X' and self.meta['date1904'] and d['changes'][col]<'1904-01-01':raise ValueError('1904日期系统不接受1904年前的新日期，原异常值保持不变')
            result=self.fields.update(key,**args,expected=d['expected'],changes=d['changes'],closing_approved=d.get('closing_approved',d['changes'].get('V')=='结案通过'))
            if d.get('voice_proposal_id'):
                with self.registry.connect() as db:db.execute('UPDATE voice_pending SET completed=1 WHERE id=?',(d['voice_proposal_id'],))
            return result
        if path=='undo':return self.fields.undo(key,**args,target_request=d['target_request'])
        raise ValueError('未知动作')
    def export(self):
        changes=[];fields=[];session=self.active['generation'];signature=self.operation_signature()
        with self.registry.connect() as db:
            for r in self.rows:
                bid=record_identity(r)
                n=db.execute('SELECT * FROM d_ops WHERE session=? AND business=? ORDER BY rowid DESC LIMIT 1',(session,bid)).fetchone()
                if n:changes.append(dict(request_id=n['request'],params_fingerprint=n['params'],business_id=bid,row=r['row'],expected_number=r['number']))
                f=db.execute('SELECT * FROM f_ops WHERE session=? AND business=? ORDER BY rowid DESC LIMIT 1',(session,bid)).fetchone()
                if f:fields.append(dict(request_id=f['request'],params_fingerprint=f['params'],business_id=bid,row=r['row']))
        out=self.source.parent/('仅供测试_禁止回传WPS_'+uuid.uuid4().hex[:10]+'.xlsx')
        result=export_copy(self.source,out,expected_sha256=self.active['sha'],registry=self.registry,changes=changes,journal=self.fields,field_changes=fields)
        if signature!=self.operation_signature():raise ValueError('导出期间本地状态改变；输出仅是旧状态，需重新导出')
        with sqlite3.connect(str(self.catalog)) as db:db.execute('INSERT INTO exports VALUES(?,?,?,?)',(session,signature,result['path'],result['sha256']))
        return result

def handler(work):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,code,data,typ='application/json; charset=utf-8'):
            raw=json.dumps(data,ensure_ascii=False).encode() if not isinstance(data,bytes) else data
            trace=uuid.uuid4().hex
            self.send_response(code);self.send_header('X-R3-Request-ID',trace);self.send_header('Content-Type',typ);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            logging.getLogger('r3.request').info(json.dumps(dict(event='http_response',pid=os.getpid(),request_id=trace,client_revision=getattr(self,'client_revision',None),path=self.path,status=code,elapsed_ms=round((time.perf_counter()-getattr(self,'trace_start',time.perf_counter()))*1000,2),query_sha256=getattr(self,'query_sha256',None),query_input=getattr(self,'query_input',None),scope_count=getattr(self,'scope_count',None),scope_source=data.get('scope_source') if isinstance(data,dict) else None,selected_row=getattr(self,'selected_row',None),result_count=data.get('count') if isinstance(data,dict) else None,error=data.get('error') if isinstance(data,dict) else None),ensure_ascii=False))
        def do_GET(self):
            self.trace_start=time.perf_counter()
            if self.headers.get('Host') not in ('127.0.0.1:8877','localhost:8877',f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):return self.send(403,dict(error='无效Host'))
            if self.path=='/api/status':return self.send(200,work.status())
            if self.path=='/':return self.send(200,(Path(__file__).parent/'web/r3.html').read_bytes(),'text/html; charset=utf-8')
            self.send(404,dict(error='未找到'))
        def do_POST(self):
            self.trace_start=time.perf_counter();self.query_sha256=None;self.query_input=None;self.scope_count=None;self.selected_row=None;self.client_revision=None
            if self.headers.get('Host') not in ('127.0.0.1:8877','localhost:8877',f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):return self.send(403,dict(error='无效Host'))
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):return self.send(403,dict(error='拒绝跨站'))
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<1 or size>20_000_000:raise ValueError('请求大小无效')
                raw=self.rfile.read(size)
                if self.path=='/api/stage':result=work.stage(raw)
                else:
                    d=json.loads(raw)
                    self.client_revision=d.get('client_revision')
                    if isinstance(d.get('query'),str):
                        self.query_sha256=hashlib.sha256(d['query'].encode()).hexdigest();self.query_input=d['query'];self.scope_count=len(d['scope']) if isinstance(d.get('scope'),list) else None
                    self.selected_row=d.get('row')
                    result=work.commit(d) if self.path=='/api/commit' else work.action(self.path.removeprefix('/api/'),d)
                self.send(200,result)
            except Exception as e:self.send(400,dict(error=str(e),version=work.status()['version']))
    return Handler
if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(message)s',stream=sys.stderr)
    def exiting(signum,frame):
        logging.getLogger('r3.lifecycle').info(json.dumps(dict(event='service_signal',pid=os.getpid(),signal=signum,time=time.time())))
        raise SystemExit(0)
    for signum in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(signum,exiting)
    work=Workbench();logging.getLogger('r3.lifecycle').info(json.dumps(dict(event='service_start',pid=os.getpid(),ppid=os.getppid(),sid=os.getsid(0),time=time.time(),version=work.status()['version'],count=len(work.rows))))
    print('R3 http://127.0.0.1:8877 隔离测试，未同步',flush=True)
    HTTPServer(('127.0.0.1',8877),handler(work)).serve_forever()
