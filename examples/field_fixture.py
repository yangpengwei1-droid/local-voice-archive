"""Synthetic-only G/V/X workbook fixture."""
import io
import zipfile
from examples.workbook_fixture import fixture

def field_fixture(date1904=False,original_date=''):
    if original_date and not str(original_date).isdigit():raise ValueError('synthetic date must be numeric')
    result=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(fixture())) as source,zipfile.ZipFile(result,'w') as target:
        for name in source.namelist():
            data=source.read(name)
            if name=='xl/styles.xml':
                data=data.replace(b'<cellXfs count="2">',b'<cellXfs count="3">').replace(b'</cellXfs>',b'<xf numFmtId="14" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/></cellXfs>')
            if name=='xl/workbook.xml' and date1904:data=data.replace(b'<sheets>',b'<workbookPr date1904="1"/><sheets>')
            if name=='xl/worksheets/sheet1.xml':
                data=data.replace(b'<c r="F2" s="1"/>',b'<c r="F2" s="1"/><c r="G2"/>').replace(b'</row><row r="3">',b'<c r="V2"/><c r="X2" s="2"/></row><row r="3">')
                data=data.replace(b'<c r="F3" t="inlineStr"><is><t>2031001</t></is></c>',b'<c r="F3" t="inlineStr"><is><t>2031001</t></is></c><c r="G3"/>')
                data=data.replace(b'</row></sheetData>',b'<c r="V3"/><c r="X3" s="2"/></row></sheetData>')
                if original_date:data=data.replace(b'<c r="X2" s="2"/>',('<c r="X2" s="2"><v>'+str(original_date)+'</v></c>').encode())
                data=data.replace(b'count="1"><dataValidation',b'count="2"><dataValidation').replace(b'</dataValidations>','<dataValidation type="list" sqref="V2:V3"><formula1>"结案通过,结案不通过,无须结案"</formula1></dataValidation></dataValidations>'.encode())
            target.writestr(name,data)
    return result.getvalue()

