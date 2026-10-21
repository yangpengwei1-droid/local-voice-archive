"""Synthetic G/V/X updates with validation, append-only logs and readback."""
import datetime
import json
import re
from local_registry import fingerprint,RegistrationError


def normalize_date_input(value):
    """Accept explicit numeric dates while storing one ISO business value."""
    import unicodedata
    if not isinstance(value,str):raise ValueError('签订日期须为完整年月日')
    m=re.fullmatch(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?',unicodedata.normalize('NFKC',value).strip())
    if not m:raise ValueError('签订日期须为完整年月日，如2026年7月11日或2026/7/11')
    date=datetime.date(*map(int,m.groups()))
    if date.year<1900:raise ValueError('日期早于Excel支持范围')
    return date.isoformat()

def display_date(value,date1904=False):
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value or ''):return value
    if value and re.search(r'[年月/-]',value):
        try:return normalize_date_input(value)
        except ValueError:return ''
    if not value:return ''
    try:
        serial=int(float(value))
        if not date1904 and serial==60:return '' # Excel's fictitious 1900-02-29.
        epoch=datetime.date(1904,1,1) if date1904 else datetime.date(1899,12,31) if serial<60 else datetime.date(1899,12,30)
        return (epoch+datetime.timedelta(days=serial)).isoformat()
    except (ValueError,OverflowError):return ''


def target_rules(raw,row):
    from app import read_xlsx
    validations=read_xlsx(raw)[1]['validations'];result={}
    for col in ('G','V'):
        lists=[]
        for validation in validations:
            for area in validation['range'].replace('$','').split():
                match=re.fullmatch(r'([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?',area)
                if match and match[1]<=col<=(match[3] or match[1]) and int(match[2])<=row<=int(match[4] or match[2]):
                    formula=validation['formula']
                    if validation.get('type')!='list' or not(formula.startswith('"') and formula.endswith('"')):raise RegistrationError('目标下拉不是已支持的显式列表')
                    lists.append(formula[1:-1].split(','))
        if not lists:raise RegistrationError('目标单元格没有有效下拉规则，拒绝更新')
        result[col]=[value for value in lists[0] if all(value in options for options in lists)]
    return result


class FieldJournal:
    def __init__(self,registry):
        self.registry=registry
        with registry.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS f_base(session TEXT,business TEXT,identity TEXT,originals TEXT,rules TEXT,PRIMARY KEY(session,business));
            CREATE TABLE IF NOT EXISTS f_current(session TEXT,business TEXT,values_json TEXT,PRIMARY KEY(session,business));
            CREATE TABLE IF NOT EXISTS f_ops(request TEXT PRIMARY KEY,params TEXT,session TEXT,business TEXT,identity TEXT,old_json TEXT,new_json TEXT,kind TEXT,target TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TRIGGER IF NOT EXISTS f_ops_no_update BEFORE UPDATE ON f_ops BEGIN SELECT RAISE(ABORT,'append only'); END;
            CREATE TRIGGER IF NOT EXISTS f_ops_no_delete BEFORE DELETE ON f_ops BEGIN SELECT RAISE(ABORT,'append only'); END;
            ''')

    def initialize(self,*,session,business_id,identity_fingerprint,originals,rules):
        if set(originals) not in ({'G','V','X'},{'G','U','V','X'}) or set(rules)!=(set(originals)-{'X'}) or any(not isinstance(originals[c],str) for c in originals):raise RegistrationError('需要G/V/X原值与目标G/V有效列表')
        if any(not isinstance(rules[c],list) or not rules[c] or any(not isinstance(v,str) or not v for v in rules[c]) for c in rules):raise RegistrationError('空或未知验证列表不允许写入')
        self.registry.backup()
        with self.registry.connect() as db:
            record=db.execute('SELECT identity FROM d_records WHERE session=? AND business=?',(session,business_id)).fetchone()
            if not record or record['identity']!=identity_fingerprint:raise RegistrationError('字段初始化身份不一致')
            # No automatic upsert: repeated initialization cannot reset saved state.
            db.execute('INSERT INTO f_base VALUES(?,?,?,?,?)',(session,business_id,identity_fingerprint,json.dumps(originals),json.dumps(rules)))
            db.execute('INSERT INTO f_current VALUES(?,?,?)',(session,business_id,json.dumps(originals)))

    @staticmethod
    def with_returned(values, originals):
        result=dict(values)
        if 'U' in originals and 'U' not in result:result['U']=originals['U']
        return result

    def add_returned_baseline(self,*,session,business_id,identity_fingerprint,value,choices):
        """Enrich only mutable baseline/current JSON; never rewrite old f_ops."""
        self.registry.backup()
        with self.registry.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT b.identity,b.originals,b.rules,c.values_json FROM f_base b JOIN f_current c ON c.session=b.session AND c.business=b.business WHERE b.session=? AND b.business=?',(session,business_id)).fetchone()
            if not row or row['identity']!=identity_fingerprint:raise RegistrationError('返还字段身份不一致')
            original=json.loads(row['originals']);current=json.loads(row['values_json']);rules=json.loads(row['rules'])
            if 'U' in original:
                if original['U']!=value:raise RegistrationError('返还字段基线冲突')
                return
            original['U']=value;current['U']=value;rules['U']=choices or ['__UNAVAILABLE__']
            db.execute('UPDATE f_base SET originals=?,rules=? WHERE session=? AND business=?',(json.dumps(original),json.dumps(rules),session,business_id))
            db.execute('UPDATE f_current SET values_json=? WHERE session=? AND business=?',(json.dumps(current),session,business_id))

    def update(self,key,*,session,baseline,business_id,identity_fingerprint,expected,changes,closing_approved=False):
        if not changes or not set(changes)<= {'G','U','V','X'} or set(expected)!=set(changes):raise RegistrationError('仅允许明确G/V/X变更且逐项提供原值')
        args=dict(session=session,baseline=baseline,business_id=business_id,identity_fingerprint=identity_fingerprint,expected=expected,changes=changes,closing_approved=closing_approved)
        return self._save(key,args,'update')

    def current_state(self,*,session,business_id):
        with self.registry.connect() as db:
            row=db.execute('SELECT b.*,c.values_json FROM f_base b JOIN f_current c ON c.session=b.session AND c.business=b.business WHERE b.session=? AND b.business=?',(session,business_id)).fetchone()
            operation=db.execute('SELECT request,kind FROM f_ops WHERE session=? AND business=? ORDER BY rowid DESC LIMIT 1',(session,business_id)).fetchone()
        if not row:raise RegistrationError('字段状态尚未初始化')
        return dict(original_fields=json.loads(row['originals']),current_fields=json.loads(row['values_json']),rules=json.loads(row['rules']),request_id=operation['request'] if operation else None,operation_kind=operation['kind'] if operation else None,online_state='not_synced')

    def undo(self,key,*,target_request,session,baseline,business_id,identity_fingerprint):
        args=dict(target_request=target_request,session=session,baseline=baseline,business_id=business_id,identity_fingerprint=identity_fingerprint)
        return self._save(key,args,'undo')

    def _save(self,key,args,kind):
        if not key:raise RegistrationError('字段更新需要幂等键')
        digest=fingerprint(args);self.registry.backup();db=self.registry.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM f_ops WHERE request=?',(key,)).fetchone()
            if previous:
                if previous['params']!=digest:raise RegistrationError('字段幂等键异参冲突')
                db.rollback()
            else:
                s=db.execute('SELECT baseline FROM d_sessions WHERE id=?',(args['session'],)).fetchone()
                base=db.execute('SELECT * FROM f_base WHERE session=? AND business=?',(args['session'],args['business_id'])).fetchone()
                if not s or s['baseline']!=args['baseline'] or not base or base['identity']!=args['identity_fingerprint']:raise RegistrationError('字段基线/身份冲突')
                current=json.loads(db.execute('SELECT values_json FROM f_current WHERE session=? AND business=?',(args['session'],args['business_id'])).fetchone()[0])
                old=dict(current)
                if kind=='undo':
                    target=db.execute('SELECT * FROM f_ops WHERE request=?',(args['target_request'],)).fetchone()
                    originals=json.loads(base['originals'])
                    normalized_new=self.with_returned(json.loads(target['new_json']),originals) if target else None
                    if not target or target['session']!=args['session'] or target['business']!=args['business_id'] or current!=normalized_new:raise RegistrationError('字段撤销目标已变化，拒绝覆盖')
                    new=self.with_returned(json.loads(target['old_json']),originals)
                else:
                    rules=json.loads(base['rules']);new=dict(current)
                    for col,value in args['changes'].items():
                        if not isinstance(value,str) or not value:raise RegistrationError('不接受空字段更新')
                        if col not in current:raise RegistrationError('目标字段尚未初始化')
                        if current[col]!=args['expected'][col]:raise RegistrationError('字段原值冲突')
                        if col in ('G','U','V') and value not in rules.get(col,[]):raise RegistrationError('值不属于目标单元格有效列表')
                        if col=='V' and value=='结案通过' and args['closing_approved'] is not True:raise RegistrationError('有报告不等于通过，需要明确结案通过确认')
                        if col=='X':
                            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise RegistrationError('签订日期必须明确完整年份YYYY-MM-DD')
                            try:date=datetime.date.fromisoformat(value)
                            except ValueError:raise RegistrationError('无效日期或闰日')
                            if date.year<1900:raise RegistrationError('日期早于Excel支持范围')
                        new[col]=value
                db.execute('INSERT INTO f_ops(request,params,session,business,identity,old_json,new_json,kind,target) VALUES(?,?,?,?,?,?,?,?,?)',
                    (key,digest,args['session'],args['business_id'],args['identity_fingerprint'],json.dumps(old),json.dumps(new),kind,args.get('target_request')))
                db.execute('UPDATE f_current SET values_json=? WHERE session=? AND business=?',(json.dumps(new),args['session'],args['business_id']))
                self.registry.fault('before_save');db.commit();self.registry.fault('after_commit')
        except Exception:db.rollback();raise
        finally:db.close()
        return self.read_result(key,digest)

    def read_result(self,key,expected_params):
        self.registry.fault('before_readback')
        with self.registry.connect() as db:
            r=db.execute('''SELECT o.*,b.originals,c.values_json,s.baseline FROM f_ops o
             JOIN f_base b ON b.session=o.session AND b.business=o.business
             JOIN f_current c ON c.session=o.session AND c.business=o.business
             JOIN d_sessions s ON s.id=o.session WHERE o.request=?''',(key,)).fetchone()
        if not r or r['params']!=expected_params or self.with_returned(json.loads(r['new_json']),json.loads(r['originals']))!=json.loads(r['values_json']):raise RegistrationError('字段保存回读不一致或已被后续更新')
        return dict(state='local_fields_saved',online_state='not_synced',request_id=key,business_id=r['business'],identity_fingerprint=r['identity'],baseline=r['baseline'],
                    original_fields=json.loads(r['originals']),current_fields=json.loads(r['values_json']),operation_kind=r['kind'],message='字段撤销已保存到本地并回读，尚未同步线上' if r['kind']=='undo' else '字段已保存到本地并回读，尚未同步线上')
