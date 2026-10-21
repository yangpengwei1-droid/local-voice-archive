"""R3 durable isolated-test domain registry. Does not write workbook inputs."""
import json
import re
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from local_registry import LocalRegistry, RegistrationError, fingerprint


STORAGE=Path(__file__).resolve().parent/'var'/'runtime'


class R3Registry(LocalRegistry):
    def __init__(self,path,fault=None):
        self.path=Path(path).resolve()
        if self.path.suffix!='.sqlite' or not any(p.resolve() in self.path.parents for p in (STORAGE,Path('/tmp'),Path(tempfile.gettempdir()))):
            raise RegistrationError('R3登记仅允许独立持久存储目录')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.fault=fault or (lambda stage:None)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS d_sessions(id TEXT PRIMARY KEY,baseline TEXT,workbook TEXT,domain TEXT,config TEXT,confirmation TEXT);
            CREATE TABLE IF NOT EXISTS d_records(session TEXT,business TEXT,identity TEXT,original TEXT,PRIMARY KEY(session,business));
            CREATE TABLE IF NOT EXISTS d_business(workbook TEXT,domain TEXT,business TEXT,identity TEXT,number TEXT,status TEXT,PRIMARY KEY(workbook,domain,business));
            CREATE TABLE IF NOT EXISTS d_used(workbook TEXT,domain TEXT,number TEXT,PRIMARY KEY(workbook,domain,number));
            CREATE TABLE IF NOT EXISTS d_conflicts(workbook TEXT,domain TEXT,number TEXT,PRIMARY KEY(workbook,domain,number));
            CREATE TABLE IF NOT EXISTS d_ops(request TEXT PRIMARY KEY,params TEXT,session TEXT,business TEXT,identity TEXT,number TEXT,previous TEXT,kind TEXT,target TEXT,reason TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS voice_confirmation_gates(proposal TEXT PRIMARY KEY,expires REAL);
            CREATE TABLE IF NOT EXISTS voice_pending(id TEXT PRIMARY KEY,session TEXT,row INTEGER,identity TEXT,kind TEXT,payload TEXT,completed INTEGER);
            CREATE TRIGGER IF NOT EXISTS d_ops_no_update BEFORE UPDATE ON d_ops BEGIN SELECT RAISE(ABORT,'append only'); END;
            CREATE TRIGGER IF NOT EXISTS d_ops_no_delete BEFORE DELETE ON d_ops BEGIN SELECT RAISE(ABORT,'append only'); END;
            CREATE TRIGGER IF NOT EXISTS d_used_no_delete BEFORE DELETE ON d_used BEGIN SELECT RAISE(ABORT,'used numbers cannot be recycled'); END;
            CREATE TRIGGER IF NOT EXISTS d_used_no_update BEFORE UPDATE ON d_used BEGIN SELECT RAISE(ABORT,'used numbers immutable'); END;
            ''')

    def create_session(self,session,*,baseline,workbook_id,domain,year,first,last,width,confirmation_id,jointly_processing,no_other_numbering,records):
        if not (jointly_processing is True and no_other_numbering is True and confirmation_id and workbook_id and domain):
            raise RegistrationError('需要显式本次会话确认及稳定工作簿身份/编号域')
        if not re.fullmatch('[0-9a-f]{64}',baseline) or not (type(year)is int and 2000<=year<=9999 and type(width)is int and 0<=width<=8 and type(first)is int and type(last)is int and 1<=first<=last<(10**width if width else 100000000)):
            raise RegistrationError('基线或年度号段规则无效')
        if len({r['business_id'] for r in records})!=len(records):raise RegistrationError('重复业务身份')
        for r in records:
            if not r.get('business_id') or not re.fullmatch('[0-9a-f]{64}',r.get('identity_fingerprint','')) or not isinstance(r.get('number',''),str):
                raise RegistrationError('业务身份或原号文本无效')
        self.backup();db=self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO d_sessions VALUES(?,?,?,?,?,?)',(session,baseline,workbook_id,domain,json.dumps(dict(year=year,first=first,last=last,width=width)),confirmation_id))
            counts=Counter(r.get('number') for r in records if r.get('number'))
            for number,count in counts.items():
                if count>1:db.execute('INSERT OR IGNORE INTO d_conflicts VALUES(?,?,?)',(workbook_id,domain,number))
            for r in records:
                business,identity,original=r['business_id'],r['identity_fingerprint'],r.get('number') or None
                current=db.execute('SELECT * FROM d_business WHERE workbook=? AND domain=? AND business=?',(workbook_id,domain,business)).fetchone()
                if current and (current['identity']!=identity or (original and original!=current['number'])):
                    raise RegistrationError('跨会话业务身份或原号冲突，不能覆盖')
                if original:
                    other=db.execute('SELECT 1 FROM d_business WHERE workbook=? AND domain=? AND number=? AND business<>?',(workbook_id,domain,original,business)).fetchone()
                    if other:db.execute('INSERT OR IGNORE INTO d_conflicts VALUES(?,?,?)',(workbook_id,domain,original))
                    db.execute('INSERT OR IGNORE INTO d_used VALUES(?,?,?)',(workbook_id,domain,original))
                if not current:db.execute('INSERT INTO d_business VALUES(?,?,?,?,?,?)',(workbook_id,domain,business,identity,original,'historical' if original else 'blank'))
                db.execute('INSERT INTO d_records VALUES(?,?,?,?)',(session,business,identity,original))
            self.fault('before_session_save');db.commit()
        except Exception:db.rollback();raise
        finally:db.close()
        return self.history_report(session)

    def history_report(self,session):
        with self.connect() as db:
            s=db.execute('SELECT * FROM d_sessions WHERE id=?',(session,)).fetchone()
            if not s:raise RegistrationError('未知会话')
            numbers=[r[0] for r in db.execute('SELECT number FROM d_conflicts WHERE workbook=? AND domain=? ORDER BY number',(s['workbook'],s['domain']))]
        return dict(historical_duplicate_numbers=numbers,new_allocation_blocked=bool(numbers),message='历史重复仅报告，未改号或去重')

    def preview(self,session,year=None):
        with self.connect() as db:
            s=db.execute('SELECT * FROM d_sessions WHERE id=?',(session,)).fetchone()
            if not s:raise RegistrationError('未知会话')
            try:return dict(number=self._next(db,self.year_rule(s,year)),reserved=False,test_only=True)
            except RegistrationError as e:return dict(number=None,reserved=False,test_only=True,unavailable=str(e))

    @staticmethod
    def year_rule(s,year):
        if year is None:return s
        if type(year)is not int or not 2000<=year<=2099:raise RegistrationError('登记年度无效')
        s=dict(s);rule=json.loads(s['config']);rule.update(year=year,min_digits=3,width=0);s['config']=json.dumps(rule);return s

    def _next(self,db,s):
        rule=json.loads(s['config'])
        if rule.get('min_digits'):
            # Historical shared file numbers are occupied values, not an annual freeze.
            # Used numbers and append-only operation history (including voided numbers)
            # participate in the same maximum. Never reuse an old number.
            numbers={r[0] for r in db.execute('SELECT number FROM d_used WHERE workbook=? AND domain=?',(s['workbook'],s['domain']))}
            for r in db.execute('SELECT o.number,o.previous FROM d_ops o JOIN d_sessions t ON t.id=o.session WHERE t.workbook=? AND t.domain=?',(s['workbook'],s['domain'])):
                numbers.update(n for n in r if n)
            seqs=set()
            for n in numbers:
                if not n.startswith(str(rule['year'])):continue
                if not re.fullmatch(r'\d+',n[4:]) or int(n[4:])<1:raise RegistrationError('所选年度有异常编号，请人工核对；历史号不改')
                seqs.add(int(n[4:]))
            seq=max(seqs,default=0)+1
            if seq>rule['last']:raise RegistrationError('所选年度序号范围已用完')
            return str(rule['year'])+str(seq).zfill(rule['min_digits'])
        if db.execute('SELECT 1 FROM d_conflicts WHERE workbook=? AND domain=?',(s['workbook'],s['domain'])).fetchone():
            raise RegistrationError('编号域有历史重复冲突，阻断该域新增；原号不改')
        rule=json.loads(s['config'])
        if rule['width']==0:
            seqs=[]
            for row in db.execute('SELECT number FROM d_used WHERE workbook=? AND domain=?',(s['workbook'],s['domain'])):
                number=row[0]
                if number.startswith(str(rule['year'])):
                    suffix=number[4:]
                    if not re.fullmatch('[1-9][0-9]*',suffix):raise RegistrationError('当年已有异常或补零编号，禁止推断下一号')
                    seqs.append(int(suffix))
            seq=max(seqs,default=0)+1
            if seq>rule['last']:raise RegistrationError('当年序号范围已用完')
            return str(rule['year'])+str(seq)
        for seq in range(rule['first'],rule['last']+1):
            number=str(rule['year'])+str(seq).zfill(rule['width'])
            if not db.execute('SELECT 1 FROM d_used WHERE workbook=? AND domain=? AND number=?',(s['workbook'],s['domain'],number)).fetchone():
                return number
        raise RegistrationError('显式号段已用完')

    def _allocate(self,db,s):
        number=self._next(db,s)
        db.execute('INSERT INTO d_used VALUES(?,?,?)',(s['workbook'],s['domain'],number))
        return number

    def register(self,request_id,*,session,baseline,business_id,identity_fingerprint,allocation_year=None):
        args=dict(session=session,baseline=baseline,business_id=business_id,identity_fingerprint=identity_fingerprint)
        if allocation_year is not None:args['allocation_year']=allocation_year
        return self._operate(request_id,args,'register')

    def set_number(self,request_id,*,session,baseline,business_id,identity_fingerprint,new_number,expected_number):
        return self._operate(request_id,dict(session=session,baseline=baseline,business_id=business_id,identity_fingerprint=identity_fingerprint,new_number=new_number,expected_number=expected_number),'set_number')

    def current_state(self,*,session,baseline,business_id,identity_fingerprint):
        with self.connect() as db:
            row=db.execute('''SELECT b.*,r.original,s.baseline FROM d_records r JOIN d_sessions s ON s.id=r.session
             JOIN d_business b ON b.workbook=s.workbook AND b.domain=s.domain AND b.business=r.business
             WHERE r.session=? AND r.business=?''',(session,business_id)).fetchone()
            op=db.execute('SELECT o.* FROM d_ops o JOIN d_sessions t ON t.id=o.session JOIN d_sessions s ON s.id=? WHERE o.business=? AND t.workbook=s.workbook AND t.domain=s.domain ORDER BY o.rowid DESC LIMIT 1',(session,business_id)).fetchone()
        if not row or row['baseline']!=baseline or row['identity']!=identity_fingerprint:raise RegistrationError('当前状态身份或基线冲突')
        return dict(business_id=business_id,number=row['number'],state=row['status'],request_id=op['request'] if op else None,
                    original_number=row['original'],is_new_number=bool(row['number'] and row['number']!=row['original']),
                    online_state='not_synced',message='已登记到本地，尚未同步线上' if row['status']=='registered' else ('已撤销，旧号不回收' if row['status']=='voided' else '原台账编号' if row['number'] else '尚未分配编号'))

    def amend(self,request_id,*,session,baseline,business_id,identity_fingerprint,target_request,action,reason):
        if action not in ('void','correct') or not reason.strip():raise RegistrationError('更正/撤销需要动作和原因')
        args=dict(session=session,baseline=baseline,business_id=business_id,identity_fingerprint=identity_fingerprint,target_request=target_request,action=action,reason=reason)
        return self._operate(request_id,args,action)

    def _operate(self,key,args,kind):
        if not key:raise RegistrationError('需要幂等键')
        params=fingerprint(args);self.backup();db=self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            previous_op=db.execute('SELECT * FROM d_ops WHERE request=?',(key,)).fetchone()
            if previous_op:
                if previous_op['params']!=params:raise RegistrationError('幂等键异参冲突')
                db.rollback()
            else:
                s=db.execute('SELECT * FROM d_sessions WHERE id=?',(args['session'],)).fetchone()
                if not s or s['baseline']!=args['baseline']:raise RegistrationError('基线会话冲突')
                record=db.execute('SELECT * FROM d_records WHERE session=? AND business=?',(args['session'],args['business_id'])).fetchone()
                b=db.execute('SELECT * FROM d_business WHERE workbook=? AND domain=? AND business=?',(s['workbook'],s['domain'],args['business_id'])).fetchone()
                if not record or not b or record['identity']!=args['identity_fingerprint'] or b['identity']!=args['identity_fingerprint']:raise RegistrationError('业务身份冲突')
                old=b['number'];number=old
                if kind=='register':
                    if b['status']=='voided':raise RegistrationError('已撤销，禁止普通登记复活；本原型不支持恢复，请单独核对')
                    if not number:number=self._allocate(db,self.year_rule(s,args.get('allocation_year')))
                elif kind=='set_number':
                    if b['status']=='voided':raise RegistrationError('已撤销记录不可用更正编号复活')
                    rule=json.loads(s['config'])
                    if rule['width']!=0 or not re.fullmatch(str(rule['year'])+r'[1-9]\d*',args['new_number']):raise RegistrationError('编号必须符合当年不补零规则')
                    if old!=args['expected_number']:raise RegistrationError('更正前编号已改变')
                    number=args['new_number']
                    if number==old or db.execute('SELECT 1 FROM d_used WHERE workbook=? AND domain=? AND number=?',(s['workbook'],s['domain'],number)).fetchone():raise RegistrationError('编号已占用，禁止更正冲突')
                    db.execute('INSERT INTO d_used VALUES(?,?,?)',(s['workbook'],s['domain'],number))
                else:
                    target=db.execute('SELECT * FROM d_ops WHERE request=?',(args['target_request'],)).fetchone()
                    origin=db.execute('SELECT workbook,domain FROM d_sessions WHERE id=?',(target['session'],)).fetchone() if target else None
                    if not target or not origin or origin['workbook']!=s['workbook'] or origin['domain']!=s['domain'] or target['business']!=args['business_id'] or target['identity']!=args['identity_fingerprint'] or target['number']!=old or b['status']=='voided':raise RegistrationError('更正目标已失效或身份不一致')
                    number=None if kind=='void' else self._allocate(db,s)
                db.execute('INSERT INTO d_ops(request,params,session,business,identity,number,previous,kind,target,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (key,params,args['session'],args['business_id'],args['identity_fingerprint'],number,old,kind,args.get('target_request'),args.get('reason','')))
                db.execute('UPDATE d_business SET number=?,status=? WHERE workbook=? AND domain=? AND business=?',(number,'voided' if kind=='void' else 'registered',s['workbook'],s['domain'],args['business_id']))
                self.fault('before_save');db.commit();self.fault('after_commit')
        except Exception:db.rollback();raise
        finally:db.close()
        return self.read_result(key,params)

    def read_result(self,request_id,expected_params):
        self.fault('before_readback')
        with self.connect() as db:
            r=db.execute('''SELECT o.*,s.baseline,b.number AS actual,b.identity AS actual_identity,b.status FROM d_ops o
             JOIN d_sessions s ON s.id=o.session JOIN d_business b ON b.workbook=s.workbook AND b.domain=s.domain AND b.business=o.business
             WHERE o.request=?''',(request_id,)).fetchone()
        if not r or r['params']!=expected_params or r['identity']!=r['actual_identity'] or r['number']!=r['actual']:
            raise RegistrationError('登记回读未确认或操作已被更正；禁止将旧记录报为成功')
        voided=r['kind']=='void'
        return dict(request_id=request_id,business_id=r['business'],number=r['number'],previous_number=r['previous'],operation_kind=r['kind'],identity_fingerprint=r['identity'],baseline=r['baseline'],
                    state='voided' if voided else 'local_registered',online_state='not_synced',
                    message='已本地撤销，原号保留占用，尚未同步线上' if voided else '已登记到本地，尚未同步线上')
