"""Synthetic-only local registration prototype. Not connected to app.py or WPS.

Databases must live under the OS temporary directory or /tmp. No workbook APIs.
"""
import hashlib
import json
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path


class RegistrationError(Exception):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class LocalRegistry:
    def __init__(self, path, fault=None):
        self.path = Path(path).resolve()
        allowed = [Path(tempfile.gettempdir()).resolve(), Path('/tmp').resolve()]
        if not any(root in self.path.parents for root in allowed) or self.path.suffix != '.sqlite':
            raise RegistrationError('合成登记仅允许临时目录中的.sqlite；未接入真实数据路径')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.fault = fault or (lambda stage: None)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY, baseline TEXT NOT NULL, config TEXT NOT NULL,
              confirmation TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS records (
              session TEXT NOT NULL REFERENCES sessions(id), business TEXT NOT NULL,
              identity TEXT NOT NULL, number TEXT UNIQUE,
              PRIMARY KEY(session,business));
            CREATE TABLE IF NOT EXISTS operations (
              request TEXT PRIMARY KEY, params TEXT NOT NULL,
              session TEXT NOT NULL, business TEXT NOT NULL,
              identity TEXT NOT NULL, number TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state='local_registered'),
              online_state TEXT NOT NULL CHECK(online_state='not_synced'),
              created TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')

    def connect(self):
        db = sqlite3.connect(str(self.path),timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        return db

    def backup(self):
        self.fault('before_backup')
        directory = self.path.parent / (self.path.stem + '_backups')
        directory.mkdir(exist_ok=True)
        target = directory / (uuid.uuid4().hex+'.sqlite')
        with self.connect() as source, sqlite3.connect(str(target)) as dest:
            source.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RegistrationError('备份校验失败，未执行登记')
        return target

    def create_session(self, session, *, baseline, year, first, last, width,
                       confirmation_id, jointly_processing, no_other_numbering,
                       records):
        if not jointly_processing or not no_other_numbering or not confirmation_id:
            raise RegistrationError('需要本次共同处理/不另编号的显式确认')
        if not re.fullmatch(r'[0-9a-f]{64}',baseline):
            raise RegistrationError('需要导入基线SHA256')
        if not (type(year) is int and 2000 <= year <= 9999 and
                type(first) is int and type(last) is int and type(width) is int and
                1 <= width <= 8 and 1 <= first <= last < 10**width):
            raise RegistrationError('需显式合法年度、起止范围和序号位数')
        config = json.dumps(dict(year=year,first=first,last=last,width=width),sort_keys=True)
        imported = []
        for r in records:
            if not r.get('business_id') or not re.fullmatch(r'[0-9a-f]{64}',r.get('identity_fingerprint','')):
                raise RegistrationError('需要稳定业务身份及其指纹，不能只有行号')
            number = r.get('number')
            if number is not None and not isinstance(number,str):
                raise RegistrationError('原编号必须为文本，不允许自动补位或转换')
            imported.append((session,r['business_id'],r['identity_fingerprint'],number or None))
        self.backup()
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO sessions(id,baseline,config,confirmation) VALUES(?,?,?,?)',
                       (session,baseline,config,confirmation_id))
            db.executemany('INSERT INTO records VALUES(?,?,?,?)',imported)
            self.fault('before_session_save')
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, request_id, *, session, baseline, business_id, identity_fingerprint):
        if not request_id:
            raise RegistrationError('需要稳定请求幂等键')
        args = dict(session=session,baseline=baseline,business_id=business_id,identity_fingerprint=identity_fingerprint)
        params = fingerprint(args)
        # Backups use SQLite's coherent committed snapshot, before any mutation.
        # A backup failure must prevent this invocation from saving anything.
        self.backup()
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM operations WHERE request=?',(request_id,)).fetchone()
            if previous:
                if previous['params'] != params:
                    raise RegistrationError('幂等键异参冲突')
                db.rollback()
            else:
                sess = db.execute('SELECT * FROM sessions WHERE id=?',(session,)).fetchone()
                if not sess or sess['baseline'] != baseline:
                    raise RegistrationError('基线或会话不一致')
                record = db.execute('SELECT * FROM records WHERE session=? AND business=?',(session,business_id)).fetchone()
                if not record or record['identity'] != identity_fingerprint:
                    raise RegistrationError('业务身份不一致或未导入，禁止登记')
                number = record['number']
                if number is None:
                    rule = json.loads(sess['config'])
                    for seq in range(rule['first'],rule['last']+1):
                        candidate = str(rule['year']) + str(seq).zfill(rule['width'])
                        if not db.execute('SELECT 1 FROM records WHERE number=?',(candidate,)).fetchone():
                            number = candidate
                            break
                    if number is None:
                        raise RegistrationError('本次显式号段已用完')
                    db.execute('UPDATE records SET number=? WHERE session=? AND business=?',(number,session,business_id))
                db.execute('INSERT INTO operations(request,params,session,business,identity,number,state,online_state) VALUES(?,?,?,?,?,?,?,?)',
                           (request_id,params,session,business_id,identity_fingerprint,number,'local_registered','not_synced'))
                self.fault('before_save')
                db.commit()
                self.fault('after_commit')
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.read_result(request_id, params)

    def read_result(self, request_id, expected_params):
        # New physical connection: never return registration from in-memory state.
        self.fault('before_readback')
        with self.connect() as db:
            row = db.execute('''SELECT o.*,r.identity AS actual_identity,r.number AS actual_number,s.baseline
                FROM operations o JOIN records r ON r.session=o.session AND r.business=o.business
                JOIN sessions s ON s.id=o.session
                WHERE o.request=?''',(request_id,)).fetchone()
        if (not row or row['params'] != expected_params or row['identity'] != row['actual_identity']
                or row['number'] != row['actual_number']):
            raise RegistrationError('保存回读未确认，结果未知；按原幂等键查询或重试')
        return dict(request_id=request_id,business_id=row['business'],number=row['number'],
                    identity_fingerprint=row['identity'],baseline=row['baseline'],
                    state='local_registered',online_state='not_synced',
                    message='已登记到本地，尚未同步线上')
