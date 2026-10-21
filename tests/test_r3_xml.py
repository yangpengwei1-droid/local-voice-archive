import unittest,io,zipfile,xml.etree.ElementTree as ET
from r3_xml import validate,patch_cell,date_style_supported
from examples.r3_fixture import fixture
from app import read_xlsx,NS

class R3XmlTest(unittest.TestCase):
    def test_project_ordinal_equivalence_only(self):
        from r3_app import project_key
        self.assertEqual(project_key('第十三届虚构星桥创意节'),project_key('第13届虚构星桥创意节'))
        self.assertNotEqual(project_key('第十三届'),project_key('第十届'))
        self.assertNotEqual(project_key('甲公司'),project_key('乙公司'))

    def test_eight_rows_styles_formula_and_date_system(self):
        for use1904 in [False,True]:
            rows,meta=validate(fixture(use1904));self.assertEqual(len(rows),8);self.assertEqual(meta['date1904'],use1904);self.assertEqual(rows[1]['number'],'2031001');self.assertEqual(rows[0]['date'],'45000')
    def test_missing_F_G_V_inserted_in_column_order(self):
        raw=fixture(missing=True);rows,meta=validate(raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:sheet=z.read(meta['sheet_path'])
        for col,value in [('F','9999000001'),('G','已比选'),('V','结案通过')]:sheet=patch_cell(sheet,2,value,col)
        tree=ET.fromstring(sheet);cells=tree.find("s:sheetData/s:row[@r='2']",NS)
        self.assertEqual([c.attrib['r'] for c in cells],['E2','F2','G2','I2','J2','K2','M2','V2','X2'])
        self.assertEqual(cells.find("s:c[@r='K2']/s:f",NS).text,'1+2')
    def test_formula_target_rejected(self):
        with self.assertRaises(Exception):patch_cell(b'<row r="2"><c r="F2"><f>1+2</f><v>3</v></c></row>',2,'9999000001')
    def test_custom_date_exact_allowlist(self):
        xml='<styleSheet xmlns="'+NS['s']+'"><numFmts><numFmt numFmtId="176" formatCode="yyyy&quot;年&quot;m&quot;月&quot;d&quot;日&quot;;@"/></numFmts><cellXfs><xf numFmtId="176"/></cellXfs></styleSheet>'
        self.assertTrue(date_style_supported(ET.fromstring(xml),0));self.assertFalse(date_style_supported(ET.fromstring(xml.replace('日','天')),0))
