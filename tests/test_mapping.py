"""Only generated fictional files; input bytes must never change."""
import tempfile,unittest,json
from pathlib import Path
from tools.prepare_workbook import convert
from tools.workbook import build
from tools.make_demo import demo_rows
from app import read_xlsx
class MappingTest(unittest.TestCase):
 def test_new_copy_and_no_overwrite(self):
  with tempfile.TemporaryDirectory() as t:
   source=Path(t)/'source.xlsx';target=Path(t)/'new.xlsx';source.write_bytes(build(demo_rows()));before=source.read_bytes();config=json.loads((Path(__file__).parents[1]/'examples/field-map.json').read_text());self.assertEqual(convert(source,target,config),8);self.assertEqual(source.read_bytes(),before);self.assertEqual(len(read_xlsx(target.read_bytes())[0]),8)
   with self.assertRaises(ValueError):convert(source,target,config)
   with self.assertRaises(ValueError):convert(source,source,config)
