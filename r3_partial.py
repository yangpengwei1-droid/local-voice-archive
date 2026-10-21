"""Last-resort related candidates from literal field evidence, never exact hits."""
import re,unicodedata
from decimal import Decimal,InvalidOperation
from functools import lru_cache
from r3_retrieval import compact

@lru_cache(maxsize=8)
def source_index(values):
 index={}
 for i,fields in enumerate(values):
  for field in fields:
   for pos in range(len(field)-1):index.setdefault(field[pos:pos+2],set()).add(i)
 return index

def partial_retrieve(rows,text,scope=None):
 raw=re.sub(r'(?<=\d),(?=\d)', '', unicodedata.normalize('NFKC',text))
 if re.search(r'不要|不是|不能|不确定|别查|不查|改为|写入|已询价|已比选|结案|回退|撤销',raw):return None
 if re.match(r'关键词|文件编号|归档编号',raw.strip()) or re.fullmatch(r'\d+',raw.strip()):return None
 # Explicit numerical and identity distinctions are never relaxed by fragments.
 amount_matches=list(re.finditer(r'(?:金额(?:是|为)?\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(万)?(?:元)?|(\d+(?:\.\d+)?)\s*(万)?元)',raw.replace(',','')))
 if len(amount_matches)>1:return None
 amount=None
 if amount_matches:
  m=amount_matches[0];amount=Decimal(m[1] or m[3])*(10000 if m[2] or m[4] else 1);raw=raw.replace(m[0],'')
 years=set(re.findall(r'(?<!\d)20\d{2}(?!\d)',raw));editions=set(re.findall(r'第\d+届',compact(raw)))
 numbers=set(re.findall(r'\d+',raw))
 known_supplier=None;supplier_fragment=None
 m=re.search(r'(?:供应商|对应方|合作方|公司)(?:是|为|[:：])?(.+?)(?=[，,。；;]|项目(?:名称)?(?:是|为|[:：])|服务(?:内容)?(?:是|为|[:：])|$)',raw)
 if m:
  supplier_fragment=re.sub(r'有限责任公司|股份有限公司|有限公司|公司','',compact(m[1]))
  if any(compact(m[1])==compact(r['supplier']) for r in rows):known_supplier=compact(m[1])
 branch='分公司' in raw
 qualifiers=[x for x in ['主合同','补充协议','新媒体','前期'] if x in raw]
 content=re.sub(r'^(?:嗯|呃|啊|哦|麻烦|请问|请|帮我|我想|我要|查一下|找一下|查询|查找|看看|找|查)+','',raw.strip())
 content=re.sub(r'(?:项目(?:名称)?|服务(?:内容)?|供应商|对应方|合作方|公司)(?:是|为|[:：])',' ',content)
 content=re.sub(r'20\d{2}(?:年)?|第(?:[一二三四五六七八九十零两]+|\d+)届|\d+(?:周年|年)?',' ',content)
 # These wrappers contain no identifying evidence; 供应/租赁/安保 remain valid.
 content=re.sub(r'有限责任公司|股份有限公司|有限公司|公司|合同|协议|项目|服务|采购|主合同|补充协议|的|是|为',' ',content)
 chunks=[compact(x) for x in re.split(r'[^\w]+',content) if compact(x)]
 if not chunks:return None
 values=tuple(tuple(compact(r[k]) for k in ('project','title','supplier')) for r in rows)
 index=source_index(values);possible=set()
 for q in chunks:
  for i in range(len(q)-1):possible.update(index.get(q[i:i+2],()))
 hits=[]
 for i in sorted(possible):
  r=rows[i]
  if scope is not None and r['row'] not in scope:continue
  source=r['project']+' '+r['title'];c=compact(source)
  if years and not years.issubset(set(re.findall(r'20\d{2}',source))):continue
  if editions and not editions.issubset(set(re.findall(r'第\d+届',compact(r['project'])))):continue
  if any(not re.search(r'(?<!\d)'+re.escape(n)+r'(?!\d)',source) for n in numbers):continue
  if known_supplier and compact(r['supplier'])!=known_supplier:continue
  if supplier_fragment is not None and not known_supplier and not any(supplier_fragment[j:j+2] in compact(r['supplier']) for j in range(len(supplier_fragment)-1)):continue
  if branch and '分公司' not in r['supplier']:continue
  if any(x not in c for x in qualifiers if x not in ('主合同','补充协议')):continue
  if '主合同' in qualifiers and '补充' in r['title']:continue
  if '补充协议' in qualifiers and '补充' not in r['title']:continue
  if amount is not None:
   try:
    if Decimal(str(r['amount']).replace(',',''))!=amount:continue
   except InvalidOperation:continue
  matched=[];missing=[];fields={}
  for q in chunks:
   mask=[False]*len(q)
   for pos in range(len(q)):
    for end in range(len(q),pos+1,-1):
     fragment=q[pos:end];where=[k for k,v in zip(('项目','合同/服务','公司'),values[i]) if fragment in v]
     if where:
      if not all(mask[pos:end]):matched.append(fragment);fields[fragment]=where
      for n in range(pos,end):mask[n]=True
      break
   rest=''
   for ch,covered in zip(q,mask):
    if not covered:rest+=ch
    elif rest:missing.append(rest);rest=''
   if rest:missing.append(rest)
  if not matched:continue
  hits.append(dict(r,related_candidate=True,requires_disambiguation=True,approximate_service=True,matched_fragments=matched,unmatched_fragments=missing,field_evidence=fields,score=sum(len(v) for v in matched),match='相关候选（非完整匹配）；已匹配：'+'、'.join(matched)+'；未匹配：'+('、'.join(missing) or '无剩余字词，但组合条件仍需人工核对')))
 if not hits:return None
 hits.sort(key=lambda r:(-r['score'],r['row']))
 return dict(results=[],count=0,unique_exact=False,related_results=hits,related_count=len(hits),retrieval_method='partial_field_related',query_filters={'services':[],'years':sorted(years),'editions':sorted(editions),'amount':str(amount) if amount is not None else None},notice='严格检索未匹配；按项目、服务或公司中有效字词列相关候选。每份已标明匹配/未匹配词；不能仅凭通用词认定合同，必须人工选择。未跨当前范围。')
