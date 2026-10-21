"""Offline contract lookup. Python standard library only; never saves the workbook."""
import io
import json
import posixpath
import re
import time
import sys
import webbrowser
import zipfile
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parent
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
COLS = {'E':'contract','F':'number','G':'procurement','I':'project','J':'title','K':'amount','M':'supplier','U':'returned','V':'closing','X':'date'}

def read_xlsx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(x.file_size for x in z.infolist()) > 150_000_000:
            raise ValueError('解压后文件过大，请使用精简测试副本')
        book = ET.fromstring(z.read('xl/workbook.xml'))
        sheets = book.find('s:sheets', NS)
        sheet = next((s for s in sheets if s.attrib.get('name') == '总表'), sheets[0])
        rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = next(r.attrib['Target'] for r in rels if r.attrib['Id'] == rid)
        path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        tree = ET.fromstring(z.read(path))
        cells = []
        for row in tree.findall('s:sheetData/s:row', NS):
            vals = {}
            for c in row:
                ref = c.attrib.get('r', '')
                col = re.sub(r'\d', '', ref)
                v = c.find('s:v', NS)
                value = v.text if v is not None and v.text else ''
                if c.attrib.get('t') == 's' and value:
                    value = strings[int(value)]
                elif c.attrib.get('t') == 'inlineStr':
                    value = ''.join(c.find('s:is', NS).itertext())
                vals[col] = value
            cells.append((int(row.attrib['r']), vals))
        # Require the actual header signature before accepting the fixed column mapping.
        header = next((r for r,v in cells if '项目' in v.get('I','') and '合同' in v.get('J','') and '编号' in v.get('F','')), None)
        if header is None:
            raise ValueError('未识别到 I列项目名称、J列合同名称、F列文件编号表头。请核对总表结构；未导入。')
        records = [dict({'row':r}, **{key:v.get(col,'') for col,key in COLS.items()}) for r,v in cells if r > header and (v.get('I') or v.get('J'))]
        if not records:
            raise ValueError('总表没有可检索的合同记录')
        validations = [dict(range=d.attrib.get('sqref'), type=d.attrib.get('type'), formula=d.findtext('s:formula1', default='', namespaces=NS)) for d in tree.findall('s:dataValidations/s:dataValidation', NS)]
        return records, {'sheet':sheet.attrib['name'], 'header':header, 'validations':validations}

def norm(s):
    import unicodedata
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC',str(s)).lower()).replace('试听','视听')

def search(records, query, scope=None):
    # Correct an ASR grammar homophone only; never rewrite the actual company name.
    query=re.sub(r'^供应商市', '供应商是', query.strip())
    excluded = None
    if query.count('不是') > 1:
        raise ValueError('一次只支持一条纠正，请重说“不是甲公司，是乙公司”。')
    correction = re.match(r'^不是(.+?)[，,、 ]*(?:而是|是)(.+?)[。！!]?$', query.strip())
    if correction:
        excluded, positive = correction.groups()
        query = '供应商是' + positive
    elif query.strip().startswith(('不是', '不要')):
        # A negation alone supplies no positive identity; never rank the negated name.
        return []
    supplier_only = query.strip().startswith(('供应商是', '供应商：', '供应商:'))
    query = re.sub(r'^供应商[：:]', '', query.strip())
    q = norm(re.sub(r'^(帮我找一下|帮我找|供应商是|是|查一下|查找)', '', query.strip()))
    if not q:
        return []
    branch_requested='分公司' in q
    result = []
    for r in records:
        if scope is not None and r['row'] not in scope:
            continue
        if excluded and norm(r.get('supplier','')) == norm(excluded):
            continue
        if branch_requested and '分公司' not in r.get('supplier',''):
            continue # Parent company cannot stand in for a named branch.
        fields = [norm(r.get(k,'')) for k in (('supplier',) if supplier_only else ('project','title','supplier','contract','number','amount'))]
        grams = [q[i:i+2] for i in range(len(q)-1)] or [q]
        coverage = sum(any(g in f for f in fields) for g in grams) / len(grams)
        exact = any(q in f for f in fields)
        score = 1 if exact else coverage * .8 + max(SequenceMatcher(None,q,f).ratio() for f in fields) * .2
        if score >= .35:
            full_supplier = supplier_only and norm(r.get('supplier','')) == q
            result.append(dict(r, score=round(score,3), supplier_equal=full_supplier, requires_disambiguation=True, branch_requires_clarification=branch_requested and not full_supplier, match=('分公司全称相同，仍需人工核对合同' if full_supplier else '分公司近似候选，需补充分公司全称') if branch_requested else ('供应商全称相同，仍需人工核对合同' if full_supplier else ('包含关键词，请核对' if exact else '近似候选，请核对'))))
    if supplier_only and any(r['supplier_equal'] for r in result):
        result = [r for r in result if r['supplier_equal']]
    elif not supplier_only and any(r['score']==1 for r in result):
        result=[r for r in result if r['score']==1]
    return sorted(result,key=lambda r:(-r['score'],r['row']))


def search_notice(query,rows):
    if '分公司' in query and not rows:return '你说的是分公司，未找到相符分公司；母公司不能替代，请补充分公司全称或核对转写。'
    if '分公司' in query and not any(r['supplier_equal'] for r in rows):return '仅找到近似分公司，存在全称差异；请补充分公司全称，当前不能确认合同。'
    return None
