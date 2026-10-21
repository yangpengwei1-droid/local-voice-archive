"""R3 supported OOXML subset: validate before switching, patch target cells only."""
import io
import re
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from collections import Counter
from app import read_xlsx,NS
from synthetic_export import record_identity,ExportError


BUSINESS_OPTIONS={
 'G':['已询价','已比选','已招标','单一来源','废标','直接采购'],
 'U':['已返还合同','已返还合同未签日期','已返还日期未签字','已被取走'],
 'V':['结案通过','结案不通过','无须结案'],
}

def date_write_plan(styles,cell,strings):
    from field_journal import normalize_date_input,display_date
    style=int(cell.get('s','0')) if cell is not None else 0
    value=_header_value(cell,strings) if cell is not None else ''
    kind=cell.get('t','n') if cell is not None else 'n'
    if kind in ('b','e'):return dict(supported=False,mode='blocked',style=style,reason='历史X为布尔或错误值，保持原值')
    if value:
        if kind in ('s','inlineStr','str','d'):
            try:normalize_date_input(value)
            except ValueError:return dict(supported=False,reason='历史X含无法判断的文本，保持原值；请人工核对',mode='blocked',style=style)
        elif not re.fullmatch(r'\d+(?:\.\d+)?',value) or not display_date(value) or not 1900<=int(display_date(value)[:4])<=2099:
            return dict(supported=False,reason='历史X数值或类型不是可确认日期，保持原值',mode='blocked',style=style)
    if date_style_supported(styles,style):return dict(supported=True,mode='preserve',style=style,reason='保留原日期格式')
    xfs=styles.find('s:cellXfs',NS)
    if xfs is None or style>=len(xfs):return dict(supported=False,mode='blocked',style=style,reason='原日期样式索引异常，保持原值')
    target=next((int(n.get('numFmtId')) for n in styles.findall('s:numFmts/s:numFmt',NS) if n.get('formatCode')=='yyyy"年"m"月"d"日";@'),14)
    return dict(supported=True,mode='standard_date',style=style,numFmtId=target,reason='本次明确日期将按原表通用日期格式保存，仅调整此X单元格的数字格式')

def date_cell_style(styles_raw,plan):
    import copy
    tree=ET.fromstring(styles_raw);xfs=tree.find('s:cellXfs',NS)
    clone=copy.deepcopy(xfs[plan['style']]);clone.set('numFmtId',str(plan['numFmtId']));clone.set('applyNumberFormat','1')
    for i,xf in enumerate(xfs):
        if ET.tostring(xf)==ET.tostring(clone):return styles_raw,i
    index=len(xfs);pattern=re.compile(rb'(<cellXfs\b[^>]*count=")(\d+)("[^>]*>)(.*?)(</cellXfs>)',re.S);m=pattern.search(styles_raw)
    if not m:raise ExportError('日期样式表结构不支持，未写入')
    node=ET.tostring(clone,encoding='utf-8')
    return styles_raw[:m.start()]+m[1]+str(index+1).encode()+m[3]+m[4]+node+m[5]+styles_raw[m.end():],index

def col_index(col):
    value=0
    for c in col:value=value*26+ord(c)-64
    return value


def sheet_path(z,sheet_name):
    book=ET.fromstring(z.read('xl/workbook.xml'))
    sheet=next(s for s in book.find('s:sheets',NS) if s.attrib['name']==sheet_name)
    rid=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
    rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    target=next(r.attrib['Target'] for r in rels if r.attrib['Id']==rid)
    path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
    if not path.startswith('xl/worksheets/') or '..' in path:raise ExportError('工作表关系超出支持范围')
    return path


def patch_cell(sheet,row,value,column='F',numeric=False,style=None):
    address=column+str(row)
    pattern=re.compile(rb'<c\b(?=[^>]*\br="'+address.encode()+rb'")([^>]*?)(?:/>|>(.*?)</c>)',re.S)
    matches=list(pattern.finditer(sheet))
    if len(matches)>1:raise ExportError('目标单元格重复')
    attrs=matches[0].group(1) if matches else b' r="'+address.encode()+b'"'
    if matches and re.search(rb'<(?:f|extLst|phoneticPr|rPh)\b',matches[0].group(2) or b''):raise ExportError('公式或扩展目标不可写')
    attrs=re.sub(rb'\s+t="[^"]*"',b'',attrs)
    if style is not None:attrs=re.sub(rb'\s+s="[^"]*"',b'',attrs)+b' s="'+str(style).encode()+b'"'
    cell=b'<c'+attrs+(b'><v>'+value.encode()+b'</v></c>' if numeric else b' t="inlineStr"><is><t>'+escape(value).encode()+b'</t></is></c>')
    if matches:
        m=matches[0];return sheet[:m.start()]+cell+sheet[m.end():]
    match=re.search(rb'<row\b(?=[^>]*\br="'+str(row).encode()+rb'")[^>]*>(.*?)</row>',sheet,re.S)
    if not match:raise ExportError('缺失目标行，不支持创建整行')
    body=match.group(1);cells=list(re.finditer(rb'<c\b[^>]*\br="([A-Z]+)\d+"[^>]*(?:/>|>.*?</c>)',body,re.S))
    if not cells:raise ExportError('空行结构不支持')
    offset=next((m.start() for m in cells if col_index(m.group(1).decode())>col_index(column)),cells[-1].end())
    offset+=match.start(1)
    return sheet[:offset]+cell+sheet[offset:]


def _header_value(cell,strings):
    if cell is None:return ''
    value=cell.findtext('s:v',default='',namespaces=NS)
    if cell.attrib.get('t')=='s':return strings[int(value)]
    if cell.attrib.get('t')=='inlineStr':return ''.join(cell.find('s:is',NS).itertext())
    return value


def date_style_supported(styles, style):
    xfs=styles.find('s:cellXfs',NS)
    if xfs is None or style>=len(xfs):return False
    fmt=int(xfs[style].attrib.get('numFmtId','0'))
    if fmt in range(14,23):return True
    return any(int(n.attrib['numFmtId'])==fmt and n.attrib.get('formatCode')=='yyyy"年"m"月"d"日";@' for n in styles.findall('s:numFmts/s:numFmt',NS))


def validate(raw):
    rows,meta=read_xlsx(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=z.namelist()
        if len(names)!=len(set(names)):raise ExportError('重复ZIP条目')
        allowed=('_rels/','xl/','customXml/','docProps/','xl/worksheets/','xl/styles.xml','xl/sharedStrings.xml','xl/workbook.xml','xl/_rels/workbook.xml.rels','_rels/.rels','[Content_Types].xml','xl/theme/','xl/_rels/','xl/metadata.xml','xl/etsyncStorage/')
        if any(not any((n==a if a in ('xl/','_rels/') else n.startswith(a)) if a.endswith('/') else n==a for a in allowed) for n in names):raise ExportError('不支持宏、签名、图形、表对象或外部附加结构')
        for name in names:
            if name.endswith('.rels') and any(r.attrib.get('TargetMode')=='External' and r.attrib.get('Type')!='http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink' for r in ET.fromstring(z.read(name))):raise ExportError('不支持外链')
        path=sheet_path(z,meta['sheet']);xml=z.read(path);tree=ET.fromstring(xml)
        if b'<worksheet' not in xml or b'<row ' not in xml or b'<c ' not in xml or re.search(rb'<extLst(?!\s*/>)',xml):raise ExportError('不支持前缀/扩展工作表结构')
        if tree.find('s:dimension',NS) is None:raise ExportError('缺少工作表dimension，需正常xlsx副本')
        strings=[]
        if 'xl/sharedStrings.xml' in names:strings=[''.join(s.itertext()) for s in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        header=tree.find("s:sheetData/s:row[@r='%s']"%meta['header'],NS)
        aliases={'E':{'合同编号'},'F':{'文件编号'},'G':{'询价比选招标','招采状态'},'I':{'项目名称'},'J':{'合同名称'},'M':{'对应方名称','供应商名称'},'V':{'结案情况'},'X':{'合同签订日期','签订日期'}}
        for col,valid in aliases.items():
            text=_header_value(header.find("s:c[@r='%s%s']"%(col,meta['header']),NS),strings)
            if re.sub(r'[\s/／]','',text) not in valid:raise ExportError(col+'列标题不符，拒绝切换会话')
        returned_header=_header_value(header.find("s:c[@r='U%s']"%meta['header'],NS),strings).strip()
        meta['returned_supported']=returned_header=='返还情况'
        identities=Counter(record_identity(r) for r in rows)
        meta['blocked_identity_rows']=[r['row'] for r in rows if identities[record_identity(r)]>1]
        styles=ET.fromstring(z.read('xl/styles.xml'));xfs=styles.find('s:cellXfs',NS)
        cells={c.attrib['r']:c for c in tree.findall('s:sheetData/s:row/s:c',NS)}
        rules={}
        for r in rows:
            row=r['row'];rules[row]={}
            for col in ('F','G','U','V','X'):
                c=cells.get(col+str(row))
                if c is not None and (c.find('s:f',NS) is not None or c.find('s:extLst',NS) is not None):raise ExportError('目标'+col+str(row)+'含公式/扩展')
                if col=='X':
                    plan=date_write_plan(styles,c,strings)
                    rules[row]['X_supported']=plan['supported'];rules[row]['X_plan']=plan
                if col=='F' and c is not None and c.attrib.get('t') not in ('s','inlineStr','str'):
                    fmt=int(xfs[int(c.attrib.get('s','0'))].attrib.get('numFmtId','0'))
                    if r['number'] and (fmt not in (0,1,49) or not re.fullmatch(r'\d+',r['number'])):raise ExportError('数字F编号显示格式不支持，禁止猜补位')
            for col in ('G','U','V'):
                options=[]
                if col=='U' and not meta['returned_supported']:
                    rules[row][col]=[];continue
                for d in tree.findall('s:dataValidations/s:dataValidation',NS):
                    for area in d.attrib.get('sqref','').replace('$','').split():
                        m=re.fullmatch(r'([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?',area)
                        if m and col_index(m[1])<=col_index(col)<=col_index(m[3] or m[1]) and int(m[2])<=row<=int(m[4] or m[2]):
                            f=d.findtext('s:formula1',default='',namespaces=NS)
                            if m[1]!=col or (m[3] or col)!=col or d.attrib.get('type')!='list' or not(f.startswith('"') and f.endswith('"')):raise ExportError('命名/引用/跨列下拉不支持')
                            options.append(f[1:-1].split(','))
                if not options:
                    rules[row][col]=BUSINESS_OPTIONS[col][:]
                    rules[row][col+'_source']='program_standard'
                    continue
                rules[row][col+'_source']='worksheet_validation'
                rules[row][col]=[v for v in options[0] if v and all(v in o for o in options)]
                if not rules[row][col]:raise ExportError('下拉列表无有效交集')
        book=ET.fromstring(z.read('xl/workbook.xml'));props=book.find('s:workbookPr',NS)
        meta.update(sheet_path=path,date1904=props is not None and props.attrib.get('date1904') in ('1','true'),rules=rules,selection='named_total' if meta['sheet']=='总表' else 'first_sheet',missing_targets='F/G/V可在已有数据行按列序创建；X可在明确日期兼容规则下单格设置原表日期格式')
    return rows,meta
