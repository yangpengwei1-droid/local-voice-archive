"""Fresh empty runtime can bind only an explicitly selected fictional copy."""
import tempfile,unittest
from pathlib import Path
from r3_app import Workbench
from tools.workbook import build
from tools.make_demo import demo_rows
class FirstRunTest(unittest.TestCase):
 def test_first_binding_and_stale_version(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'copy.xlsx';p.write_bytes(build(demo_rows()));w=Workbench(Path(t)/'runtime');self.assertEqual(w.status()['version'],'')
   d=w.action('bind_workfile',dict(version='',path=str(p),year=2030));self.assertEqual(d['version'],w.status()['version']);self.assertEqual(len(w.rows),8)
   with self.assertRaises(Exception):w.action('bind_workfile',dict(version='',path=str(p),year=2030))
