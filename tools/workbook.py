"""Build supported template values; no real data bundled."""
import io,zipfile
from xml.sax.saxutils import escape
from examples.workbook_fixture import fixture
from r3_xml import BUSINESS_OPTIONS
COLUMNS={'registered':'A','contract':'E','number':'F','procurement':'G','project':'I','title':'J','amount':'K','supplier':'M','returned':'U','closing':'V','date':'X'}
HEADERS={'registered':'登记日期','contract':'合同编号','number':'文件编号','procurement':'询价/比选/招标','project':'项目名称','title':'合同名称','amount':'金额','supplier':'对应方名称','returned':'返还情况','closing':'结案情况','date':'合同签订日期'}
def build(rows):
 def cell(c,n,v):return f'<c r="{c}{n}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'
 sheet='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:X'+str(len(rows)+1)+'"/><sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetData><row r="1">'+''.join(cell(COLUMNS[k],1,v) for k,v in HEADERS.items())+'</row>'
 for n,row in enumerate(rows,2):sheet+=f'<row r="{n}">'+''.join(cell(c,n,row.get(k,'')) for k,c in COLUMNS.items())+'</row>'
 sheet+='</sheetData><dataValidations count="3">'
 for c,opts in BUSINESS_OPTIONS.items():sheet+=f'<dataValidation type="list" sqref="{c}2:{c}{max(2,len(rows)+1)}"><formula1>"'+','.join(opts)+'"</formula1></dataValidation>'
 sheet+='</dataValidations></worksheet>';out=io.BytesIO()
 with zipfile.ZipFile(io.BytesIO(fixture())) as source,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
  for name in source.namelist():target.writestr(name,sheet.encode() if name=='xl/worksheets/sheet1.xml' else source.read(name))
 return out.getvalue()
