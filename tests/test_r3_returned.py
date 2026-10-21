"""Fictional workbook only: returned states use explicit save and readback."""
import tempfile,unittest,uuid
from pathlib import Path
from tools.make_demo import demo_rows
from tools.workbook import build
from r3_app import Workbench
from r3_workfile import bind,sha
from r3_export import record_identity
class ReturnedTest(unittest.TestCase):
 def test_preview_save_readback_undo(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'copy.xlsx';p.write_bytes(build(demo_rows()));w=Workbench(Path(t)/'runtime');bind(w,str(p),2030)
   def action(path,**data):return w.action(path,dict(version=w.status()['version'],**data))
   identity=record_identity(w.record(2))
   for text,value in [('合同已返还','已返还合同'),('已返还合同未签日期','已返还合同未签日期'),('已返还日期未签字','已返还日期未签字'),('合同已被取走','已被取走')]:
    before=sha(p);preview=action('voice',row=2,selection_identity=identity,text=text,final=True);self.assertEqual(preview['payload']['changes'],{'U':value});self.assertEqual(sha(p),before)
    action('confirm_voice_preview',row=2,selection_identity=identity,proposal_id=preview['proposal_id']);self.assertEqual(action('current',row=2)['fields']['current_fields']['U'],value)
   cur=action('current',row=2);action('undo',row=2,selection_identity=identity,request_id=uuid.uuid4().hex,target_request=cur['fields']['request_id']);self.assertEqual(action('current',row=2)['fields']['current_fields']['U'],'已返还日期未签字')
