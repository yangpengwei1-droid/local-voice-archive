"""Map user columns to a NEW supported working copy. Values only; never edits input."""
import argparse,json,re,zipfile,posixpath,xml.etree.ElementTree as ET
from pathlib import Path
from tools.workbook import build,COLUMNS
N={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def convert(source,destination,config):
 source=Path(source);destination=Path(destination)
 if destination.exists() or source.resolve()==destination.resolve():raise ValueError('输出必须是全新路径，不能覆盖文件')
 mapping=config['columns'];header=config.get('header_row',1)
 if type(header)is not int or header<1:raise ValueError('header_row必须是正整数')
 if not {'project','title','supplier'}<=set(mapping) or set(mapping)-set(COLUMNS):raise ValueError('需配置project/title/supplier；其他字段名参见示例')
 if any(not re.fullmatch('[A-Z]{1,3}',v) for v in mapping.values()):raise ValueError('列配置须为Excel大写列字母')
 with zipfile.ZipFile(source) as z:
  if sum(i.file_size for i in z.infolist())>150_000_000:raise ValueError('文件过大')
  if any('vbaProject' in n for n in z.namelist()):raise ValueError('不接受宏工作簿')
  book=ET.fromstring(z.read('xl/workbook.xml'));sheets=book.find('s:sheets',N)
  sheet=next((s for s in sheets if s.get('name')==config.get('sheet')),None) if config.get('sheet') else sheets[0]
  if sheet is None:raise ValueError('找不到配置sheet')
  rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'));rid=sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id');target=next(r.get('Target') for r in rels if r.get('Id')==rid);path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
  strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
  tree=ET.fromstring(z.read(path))
  if tree.find('s:mergeCells',N) is not None:raise ValueError('转换工具不展开合并单元格，请先在副本中处理')
  rows=[]
  for row in tree.findall('s:sheetData/s:row',N):
   if int(row.get('r'))<=header:continue
   values={}
   for c in row:
    col=re.sub(r'\d','',c.get('r',''));v=c.findtext('s:v','',N)
    if c.get('t')=='s' and v:v=strings[int(v)]
    elif c.get('t')=='inlineStr':v=''.join(c.find('s:is',N).itertext())
    values[col]=v
   record={k:values.get(v,'') for k,v in mapping.items()}
   if record.get('project') or record.get('title'):rows.append(record)
  if not rows:raise ValueError('没有有效数据行')
 with destination.open('xb') as f:f.write(build(rows))
 return len(rows)
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('destination');p.add_argument('--mapping',required=True);a=p.parse_args();n=convert(a.source,a.destination,json.loads(Path(a.mapping).read_text()));print(f'已生成{n}行新模板副本；公式仅取缓存值，不保留原布局/样式/其他sheet。日期请用ISO文本并人工核对。')
