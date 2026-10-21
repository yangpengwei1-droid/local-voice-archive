"""Only fictional workbook content for demos and tests."""
import io
import zipfile

def fixture(unsupported=False,duplicate=False):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="总表" sheetId="1" r:id="r1"/><sheet name="其他表" sheetId="2" r:id="r2"/></sheets><definedNames><definedName name="KeepName">总表!$K$2</definedName></definedNames></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="r2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="r3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml','<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="49" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/></cellXfs></styleSheet>')
        def cell(ref,value):return '<c r="'+ref+'" t="inlineStr"><is><t>'+value+'</t></is></c>'
        rows='<row r="1">'+cell('F1','文件编号')+cell('I1','项目名称')+cell('J1','合同名称')+'</row>'
        rows+='<row r="2">'+cell('E2','合同甲')+'<c r="F2" s="1"/>'+cell('I2','合成项目')+cell('J2','车辆服务')+'<c r="K2"><f>1+2</f><v>3</v></c>'+cell('M2','虚构合成甲公司')+'</row>'
        rows+='<row r="3">'+cell('E3','合同乙')+cell('F3','2031001')+cell('I3','合成项目')+cell('J3','物料服务')+cell('M3','虚构合成乙公司')+'</row>'
        if duplicate:
            rows+='<row r="4">'+cell('E4','合同甲')+'<c r="F4" s="1"/>'+cell('I4','合成项目')+cell('J4','车辆服务')+cell('M4','虚构合成甲公司')+'</row>'
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+rows+'</sheetData><autoFilter ref="A1:X3"/><mergeCells count="1"><mergeCell ref="A5:B5"/></mergeCells><dataValidations count="1"><dataValidation type="list" sqref="G2:G3"><formula1>"已比选,已招标"</formula1></dataValidation></dataValidations></worksheet>')
        z.writestr('xl/worksheets/sheet2.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>保留其他表</t></is></c></row></sheetData></worksheet>')
        z.comment=b'preserve archive comment'
        if unsupported:z.writestr('xl/vbaProject.bin',b'unsupported')
    return out.getvalue()

