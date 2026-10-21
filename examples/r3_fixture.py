"""Representative eight-row import fixture, no personal data."""
import io,zipfile
from xml.sax.saxutils import escape
from examples.field_fixture import field_fixture

def fixture(date1904=False,missing=False):
    def cell(col,row,text,attrs=''):
        return '<c r="%s%s" %s t="inlineStr"><is><t>%s</t></is></c>'%(col,row,attrs,escape(text))
    headers={'E':'合同编号','F':'文件编号','G':'询价/比选/招标','I':'项目名称','J':'合同名称','M':'对应方名称','V':'结案情况','X':'合同签订日期'}
    sheet='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:X9"/><sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetData><row r="1">'+''.join(cell(c,1,t) for c,t in headers.items())+'</row>'
    for row in range(2,10):
        project='虚构测试项目甲' if row<6 else '虚构测试项目乙'
        sheet+='<row r="%s">'%row+cell('E',row,'测试合同'+str(row))
        if not missing or row!=2:sheet+=cell('F',row,'2031001' if row==3 else '')+cell('G',row,'')
        sheet+=cell('I',row,project)+cell('J',row,'车辆服务' if row%2 else '物料服务')+'<c r="K%s"><f>1+2</f><v>3</v></c>'%row+cell('M',row,'虚构测试甲公司' if row%2 else '虚构测试甲公司分公司')
        if not missing or row!=2:sheet+=cell('V',row,'')
        sheet+='<c r="X%s" s="2"><v>45000</v></c></row>'%row
    sheet+='</sheetData><dataValidations count="2"><dataValidation type="list" sqref="G2:G9"><formula1>"已询价,已比选,已招标"</formula1></dataValidation><dataValidation type="list" sqref="V2:V9"><formula1>"结案通过,结案不通过,无须结案"</formula1></dataValidation></dataValidations></worksheet>'
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(field_fixture(date1904))) as a,zipfile.ZipFile(out,'w') as b:
        for n in a.namelist():b.writestr(n,sheet.encode() if n=='xl/worksheets/sheet1.xml' else a.read(n))
    return out.getvalue()
