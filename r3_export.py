"""R3 restricted working-copy OOXML export. Never overwrites input; no WPS.

Unmodified ZIP entry payloads remain byte-identical. Within the target worksheet,
only explicitly matched F-cell XML is replaced; styles and all other XML bytes stay.
"""
import hashlib
import copy
import datetime
from collections import Counter
import io
import os
import posixpath
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape
from app import read_xlsx, NS
from local_registry import fingerprint, RegistrationError


class ExportError(Exception):
    pass


def record_identity(record):
    return fingerprint({k:record[k] for k in ('contract','project','title','supplier')})


def temp_path(path):
    p = Path(path).resolve()
    if not any(root in p.parents for root in (Path(tempfile.gettempdir()).resolve(),Path('/tmp').resolve(),Path(__file__).resolve().parent/'var'/'runtime')):
        raise ExportError('合成导出只允许临时目录，不接入真实台账')
    return p


from r3_xml import patch_cell as _replace_cell, date_style_supported, date_write_plan, date_cell_style, BUSINESS_OPTIONS


def export_copy(source, destination, *, expected_sha256, registry, changes, fault=None, journal=None, field_changes=None):
    """changes: [{request_id,params_fingerprint,business_id,row,expected_number}].

    The registry is independently reopened/read before a cell may be exported.
    Only synthetic paths and F cells already physically present are supported.
    """
    source, destination = temp_path(source),temp_path(destination)
    if source == destination or destination.exists() or destination.suffix != '.xlsx':
        raise ExportError('必须使用全新的.xlsx路径，不允许覆盖输入或已有文件')
    fault = fault or (lambda stage:None)
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ExportError('导出基线指纹冲突')
    before, meta = read_xlsx(raw)
    by_row = {r['row']:r for r in before}
    identity_counts = Counter(record_identity(r) for r in before)
    if len({c['request_id'] for c in changes}) != len(changes) or len({c['business_id'] for c in changes}) != len(changes):
        raise ExportError('同一登记请求或业务身份不能映射到多行')
    targets = {}
    used_numbers = set()
    for change in changes:
        row = change['row']
        if type(row) is not int or row in targets or row not in by_row:
            raise ExportError('行映射无效或目标重复')
        result = registry.read_result(change['request_id'],change['params_fingerprint'])
        if result['state'] not in ('local_registered','voided'):
            raise ExportError('登记已撤销或失效，禁止导出旧编号')
        record = by_row[row]
        if identity_counts[record_identity(record)] != 1:
            raise ExportError('源合同身份不唯一，需要显式消歧；本阶段拒绝导出')
        if result['baseline'] != expected_sha256:
            raise ExportError('登记基线与导出基线不一致')
        if result['business_id'] != change['business_id'] or result['identity_fingerprint'] != record_identity(record):
            raise ExportError('导出业务身份冲突')
        if record['number'] != change['expected_number']:
            raise ExportError('F列原值冲突')
        voided=result['state']=='voided'
        if voided and record['number'] and record['number']!=result['previous_number']:raise ExportError('撤销原号与已核对本地登记历史不符')
        if not voided and record['number'] and record['number'] != result['number'] and not (result.get('operation_kind') in ('set_number','correct') and result['previous_number']==record['number']):
            raise ExportError('本阶段禁止覆盖已有编号')
        if not voided and result['number'] in used_numbers:
            raise ExportError('不同目标不能导出同一登记号码')
        if not voided:used_numbers.add(result['number'])
        targets[row] = '' if voided else result['number']
    fields={}
    seen_field_business=set()
    for change in field_changes or []:
        row=change['row']
        if not journal or row not in by_row or row in fields or change['business_id'] in seen_field_business:raise ExportError('字段行映射无效或重复')
        result=journal.read_result(change['request_id'],change['params_fingerprint'])
        record=by_row[row]
        if result['state']!='local_fields_saved' or result['baseline']!=expected_sha256 or result['business_id']!=change['business_id'] or result['identity_fingerprint']!=record_identity(record) or identity_counts[record_identity(record)]!=1:raise ExportError('字段基线或身份冲突')
        original={'G':record['procurement'],'V':record['closing'],'X':record['date']}
        if 'U' in result['original_fields']:original['U']=record.get('returned','')
        if result['original_fields']!=original:raise ExportError('字段原值与基线冲突')
        fields[row]={col:value for col,value in result['current_fields'].items() if value!=original[col]}
        if not set(fields[row])<= {'G','U','V','X'}:raise ExportError('仅允许G/V/X字段导出')
        seen_field_business.add(change['business_id'])
    # A reconciled reimport may have no new operations; export its verified
    # baseline as a new file instead of requiring an artificial edit.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ExportError('重复ZIP条目，拒绝导出')
        # Supported fixture class: ordinary OOXML with styles/merges/filter/validation,
        # extra sheets and internal relationships. Other structures require own QA.
        allowed = ('_rels/','xl/','customXml/','docProps/','xl/worksheets/','xl/styles.xml','xl/sharedStrings.xml',
                   'xl/workbook.xml','xl/_rels/workbook.xml.rels','_rels/.rels','[Content_Types].xml','xl/theme/','xl/_rels/','xl/metadata.xml','xl/etsyncStorage/')
        if any(not any((n==a if a in ('xl/','_rels/') else n.startswith(a)) if a.endswith('/') else n==a for a in allowed) for n in names):
            raise ExportError('包含当前适配器未验收的结构（如宏、签名、图形或外部链接），拒绝导出')
        for n in names:
            if n.endswith('.rels'):
                rel = ET.fromstring(archive.read(n))
                if any(r.attrib.get('TargetMode')=='External' and r.attrib.get('Type')!='http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink' for r in rel):
                    raise ExportError('外部关系未验收，拒绝导出')
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheet = next(s for s in workbook.find('s:sheets',NS) if s.attrib['name']==meta['sheet'])
        rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target = next(r.attrib['Target'] for r in rels if r.attrib['Id']==rid)
        sheet_path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        original_sheet = archive.read(sheet_path)
        modified_sheet = original_sheet
        for row,number in targets.items():
            validated = _replace_cell(modified_sheet,row,number)
            if by_row[row]['number'] != number:
                modified_sheet = validated
        field_values={}
        sheet_tree=ET.fromstring(original_sheet)
        styles_raw=archive.read('xl/styles.xml')
        date_strings=[]
        if 'xl/sharedStrings.xml' in names:date_strings=[''.join(e.itertext()) for e in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        for row,updates in fields.items():
            field_values[row]={}
            for col,value in updates.items():
                numeric=False;style_override=None
                if col in ('G','U','V'):
                    options=[]
                    for validation in sheet_tree.findall('s:dataValidations/s:dataValidation',NS):
                        for area in validation.attrib.get('sqref','').replace('$','').split():
                            match=re.fullmatch(r'([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?',area)
                            if match and match[1]<=col<=(match[3] or match[1]) and int(match[2])<=row<=int(match[4] or match[2]):
                                formula=validation.findtext('s:formula1',default='',namespaces=NS)
                                if validation.attrib.get('type')!='list' or not (formula.startswith('"') and formula.endswith('"')):raise ExportError('目标验证规则不支持，拒绝字段导出')
                                options.append(formula[1:-1].split(','))
                    if (not options and value not in BUSINESS_OPTIONS[col]) or any(value not in option for option in options):raise ExportError('字段不属于目标单元格有效列表')
                if col=='X':
                    cell=sheet_tree.find("s:sheetData/s:row[@r='%s']/s:c[@r='X%s']"%(row,row),NS)
                    styles=ET.fromstring(archive.read('xl/styles.xml'))
                    xfs=styles.find('s:cellXfs',NS)
                    plan=date_write_plan(styles,cell,date_strings)
                    if not plan['supported']:raise ExportError(plan['reason'])
                    if plan['mode']=='standard_date':styles_raw,style_override=date_cell_style(styles_raw,plan)
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ExportError('X日期需完整年份')
                    date=datetime.date.fromisoformat(value)
                    if date.year<1900:raise ExportError('不支持1900年前日期')
                    props=workbook.find('s:workbookPr',NS)
                    date1904=props is not None and props.attrib.get('date1904') in ('1','true')
                    if date1904 and date<datetime.date(1904,1,1):raise ExportError('1904日期系统不支持基准年前的新日期')
                    serial=(date-datetime.date(1904,1,1)).days if date1904 else (date-datetime.date(1899,12,30)).days-(1 if date<datetime.date(1900,3,1) else 0)
                    value=str(serial);numeric=True
                modified_sheet=_replace_cell(modified_sheet,row,value,col,numeric,style_override)
                field_values[row][{'G':'procurement','U':'returned','V':'closing','X':'date'}[col]]=value
        ET.fromstring(modified_sheet)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload,'w') as output:
            output.comment = archive.comment
            for info in infos:
                output.writestr(copy.copy(info),modified_sheet if info.filename==sheet_path else styles_raw if info.filename=='xl/styles.xml' else archive.read(info.filename))
        exported = payload.getvalue()
        after, _ = read_xlsx(exported)
        expected = [dict(r,number=targets.get(r['row'],r['number']),**field_values.get(r['row'],{})) for r in before]
        if after != expected:
            raise ExportError('导出后单元格回读不一致')
        with zipfile.ZipFile(io.BytesIO(exported)) as verify:
            for name in names:
                if name not in (sheet_path,'xl/styles.xml') and verify.read(name) != archive.read(name):
                    raise ExportError('非目标ZIP元素改变')
    # Exclusive creation prevents a last-minute file collision; clean up on any failure.
    fault('before_write')
    created = False
    try:
        with destination.open('xb') as stream:
            created = True
            stream.write(exported)
            stream.flush()
            os.fsync(stream.fileno())
        fault('after_write')
        saved = destination.read_bytes()
        if saved != exported:
            raise ExportError('磁盘文件回读不一致')
        if source.read_bytes() != raw:
            raise ExportError('导出期间源基线变化')
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return dict(state='exported_copy_verified',online_state='not_synced',path=str(destination),
                sha256=hashlib.sha256(exported).hexdigest(),changed_rows=sorted(set(targets)|set(fields)),
                message='已导出本地测试副本并回读，尚未同步线上')
