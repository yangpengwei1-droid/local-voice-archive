import unittest,tempfile,json,datetime,zipfile
from pathlib import Path
from r3_app import Workbench
from r3_workfile import bind,sha,binding,PROTECTED
from r3_export import record_identity
from examples.r3_fixture import fixture

class WorkfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='workfile-transaction-');self.root=Path(self.tmp.name);self.w=Workbench(self.root)
        self.target=self.root/'工作副本.xlsx';self.target.write_bytes(fixture());self.original=self.target.read_bytes();bind(self.w,str(self.target),2026)
    def tearDown(self):self.tmp.cleanup()
    def action(self,path,**data):return self.w.action(path,dict(version=self.w.status()['version'],**data))
    def preview(self,text='已询价',row=2):return self.action('voice',row=row,selection_identity=record_identity(self.w.record(row)),text=text,final=True)
    def confirm(self,p,row=2):return self.action('confirm_voice_preview',row=row,selection_identity=record_identity(self.w.record(row)),proposal_id=p['proposal_id'])
    def test_confirm_direct_readback_and_undo_preservation(self):
        p=self.preview('已询价，签订日期2026年10月15日');before=sha(self.target);self.assertEqual(self.target.read_bytes(),self.original)
        result=self.confirm(p);self.assertNotEqual(sha(self.target),before);self.assertTrue(Path(result['backup']).is_file());self.assertEqual(Path(result['backup']).read_bytes(),self.original)
        from app import read_xlsx
        rows,_=read_xlsx(self.target.read_bytes());self.assertEqual(rows[0]['procurement'],'已询价')
        with zipfile.ZipFile(Path(result['backup'])) as a,zipfile.ZipFile(self.target) as b:
            for n in a.namelist():
                if n!='xl/worksheets/sheet1.xml':self.assertEqual(a.read(n),b.read(n),n)
        cur=self.action('current',row=2);self.action('undo',row=2,selection_identity=record_identity(self.w.record(2)),request_id='undo',target_request=cur['fields']['request_id'])
        self.assertEqual(read_xlsx(self.target.read_bytes())[0][0]['procurement'],'')
    def test_after_replace_recovery_and_replay(self):
        p=self.preview()
        def fault(stage):
            if stage=='after_replace':raise RuntimeError('power loss')
        self.w.workfile_fault=fault
        with self.assertRaises(RuntimeError):self.confirm(p)
        self.w=Workbench(self.root);self.assertEqual(self.action('current',row=2)['fields']['current_fields']['G'],'已询价')
        self.confirm(p)
        with self.w.registry.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM f_ops WHERE request=?',(p['proposal_id'],)).fetchone()[0],1)
    def test_before_replace_failure_does_not_save(self):
        p=self.preview()
        def fault(stage):raise RuntimeError('disk interruption')
        self.w.workfile_fault=fault
        with self.assertRaises(RuntimeError):self.confirm(p)
        self.assertEqual(self.target.read_bytes(),self.original)
        self.w=Workbench(self.root);self.assertEqual(self.action('current',row=2)['fields']['current_fields']['G'],'')
    def test_external_edit_and_lock_and_undo_conflict(self):
        p=self.preview();self.confirm(p);cur=self.action('current',row=2)
        self.target.write_bytes(self.target.read_bytes()+b'external');changed=self.target.read_bytes()
        with self.assertRaisesRegex(ValueError,'外部编辑'):self.action('undo',row=2,selection_identity=record_identity(self.w.record(2)),request_id='undo',target_request=cur['fields']['request_id'])
        self.assertEqual(self.target.read_bytes(),changed)
        config=json.loads(binding(self.w).read_text());config['sha']=sha(self.target);binding(self.w).write_text(json.dumps(config))
        lock=self.target.parent/('~$'+self.target.name);lock.touch()
        with self.assertRaisesRegex(ValueError,'占用'):self.confirm(self.preview('已比选'))
    def test_expired_cancel_wrong_contract_and_no_voice_save(self):
        p=self.preview();before=sha(self.target)
        self.assertEqual(self.action('voice',row=2,selection_identity=record_identity(self.w.record(2)),text='确认保存',final=True,proposal_id=p['proposal_id'])['kind'],'confirmation_required')
        with self.assertRaises(ValueError):self.confirm(p,4)
        with self.w.registry.connect() as db:
            payload=json.loads(db.execute('SELECT payload FROM voice_pending WHERE id=?',(p['proposal_id'],)).fetchone()[0]);payload['expires_at']=0
            db.execute('UPDATE voice_pending SET payload=? WHERE id=?',(json.dumps(payload),p['proposal_id']))
        with self.assertRaisesRegex(ValueError,'过期'):self.confirm(p)
        p=self.preview();self.action('cancel_voice_confirmation',proposal_id=p['proposal_id'],discard_preview=True)
        with self.assertRaises(ValueError):self.confirm(p)
        self.assertEqual(sha(self.target),before)
    def test_report_semantics_query_negation_and_quotes(self):
        self.assertEqual(self.preview('这份是结案报告')['payload']['changes'],{'V':'结案通过'})
        self.assertEqual(self.preview('这份是结案报告，已询价')['payload']['changes'],{'G':'已询价','V':'结案通过'})
        for text in ['他说已询价','已询价是什么','不是结案报告','查询结案报告','他说这份是结案报告','“这份是结案报告”','有结案报告','不要结案通过','结案通过吗']:
            self.assertNotEqual(self.preview(text)['kind'],'voice_preview',text)
    def test_number_and_fields_single_atomic_confirmation(self):
        self.action('set_registration_exception_year',row=2,selection_identity=record_identity(self.w.record(2)),year=2026) # fixture A is blank: explicit exception required
        p=self.preview('分配文件编号，已询价，签订日期2026年10月15日')
        number=p['payload']['allocate_number'];self.assertTrue(number.startswith('2026'))
        self.confirm(p)
        from app import read_xlsx
        row=read_xlsx(self.target.read_bytes())[0][0]
        self.assertEqual(row['number'],number);self.assertEqual(row['procurement'],'已询价')
        self.assertEqual(self.action('current',row=2)['number']['number'],number)
    def test_new_query_proposal_does_not_discard_before_ui_decision(self):
        p=self.preview()
        self.assertEqual(self.action('voice',row=2,selection_identity=record_identity(self.w.record(2)),text='重新查另一个项目',final=True)['kind'],'new_query')
        self.confirm(p)
        self.assertEqual(self.action('current',row=2)['fields']['current_fields']['G'],'已询价')
    def test_number_unique_explicit_year_and_old_domain_isolated(self):
        for row in [2,4]:self.action('set_registration_exception_year',row=row,selection_identity=record_identity(self.w.record(row)),year=2026) # fixture A is blank
        p=self.preview('分配文件编号');n=p['payload']['preview'];self.assertTrue(n.startswith('2026'));self.confirm(p)
        self.assertEqual(self.action('current',row=2)['number']['number'],n)
        q=self.preview('分配文件编号',4);self.assertNotEqual(q['payload']['preview'],n)
        self.confirm(q,4)
        with self.assertRaises(ValueError):bind(self.w,str(PROTECTED),2026)
        linked=self.root/'link.xlsx';linked.symlink_to(self.target)
        with self.assertRaises(ValueError):bind(self.w,str(linked),2026)
