"""Bounded field evidence fallback; search only, never a write intent parser."""
import re,unicodedata
from decimal import Decimal,InvalidOperation
from difflib import SequenceMatcher
from r3_retrieval import compact,core,company_core,GROUPS,NEAR

def match_fragment(q,s):
 if not q:return 0.,0
 if q in s:return 1.,len(q)
 blocks=SequenceMatcher(None,q,s,autojunk=False).get_matching_blocks()
 longest=max((b.size for b in blocks),default=0);covered=sum(b.size for b in blocks) if longest>=2 else 0
 return covered/len(q),longest

def near_company(q,s):
 if q in s:return 1.
 if len(q)<4:return 0.
 if sum(ch in s for ch in set(q))<len(set(q))-1:return 0.
 best=0.
 for n in [len(q)-1,len(q),len(q)+1]:
  for i in range(max(0,len(s)-n+1)):
   part=s[i:i+n];m=SequenceMatcher(None,q,part,autojunk=False)
   # At most one substitution/insertion/deletion; no arbitrary sound rewrite.
   i=j=edits=0
   while i<len(q) and j<len(part):
    if q[i]==part[j]:i+=1;j+=1;continue
    edits+=1
    if edits>1:break
    if len(q)>=len(part):i+=1
    if len(part)>=len(q):j+=1
   edits+=max(len(q)-i,len(part)-j)
   if edits<=1 and max((b.size for b in m.get_matching_blocks()),default=0)>=2:best=max(best,m.ratio())
 return best

def evidence_retrieve(rows,text,scope=None):
 raw=unicodedata.normalize('NFKC',text)
 if re.search(r'不要|不是|不能|不确定|别查|不查|改为|写入|不对|已询价|已比选|结案通过',raw):return None
 raw=re.sub(r'^(?:嗯|呃|啊|哦|那个|就是|麻烦|请问|请|帮我|我想|我要|查一下|找一下|查询|查找|看看|找|查)+','',raw.strip())
 raw=re.sub(r'(?:的那个|那个|就是|帮忙|谢谢|好吗|吧)(?=[，,。；;\s]|$)','',raw)
 amount_matches=list(re.finditer(r'(?:金额(?:是|为)?\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(万)?(?:元)?|(\d+(?:\.\d+)?)\s*(万)?元)',raw.replace(',','')))
 if len(amount_matches)>1:return dict(results=[],count=0,query_clarification=True,notice='存在多个金额，请明确一次查询的金额；未选择合同。')
 amount=None;residual=raw
 if amount_matches:
  m=amount_matches[0];amount=Decimal(m[1] or m[3])*(10000 if m[2] or m[4] else 1);residual=raw.replace(m[0],'')
 years=set(re.findall(r'(?<!\d)20\d{2}(?!\d)',residual));editions=set(re.findall(r'第\d+届',compact(residual)))
 residual=re.sub(r'20\d{2}(?:年(?!度))?','',residual)
 residual=re.sub(r'第(?:[一二三四五六七八九十零两]+|\d+)届','',residual)
 # Marker-delimited fields can occur in any order, with or without punctuation.
 marker=r'项目(?:名称)?|服务(?:内容)?|供应商|对应方|合作方|公司'
 supplier=None;supplier_raw=None
 m=re.search(r'(?:供应商|对应方|合作方|公司)(?:是|为|[:：])?(.+?)(?=[，,。；;]|项目(?:名称)?(?:是|为|[:：])|服务(?:内容)?(?:是|为|[:：])|$)',residual)
 if m:supplier_raw=m[1].strip();supplier=company_core(supplier_raw);residual=residual[:m.start()]+' '+residual[m.end():]
 elif re.fullmatch(r'[^，,。；;]+(?:有限公司|有限责任公司|分公司)',residual.strip()):
  supplier_raw=residual.strip();supplier=company_core(supplier_raw);residual=''
 field_project=None
 project_field=re.search(r'项目(?:名称)?(?:是|为|[:：])(.+?)(?=[，,。；;]|服务(?:内容)?(?:是|为|[:：])|$)',residual)
 if project_field:
  field_project=core(project_field[1]);residual=residual[:project_field.start()]+' '+residual[project_field.end():]
 query_content=residual
 service_texts=re.findall(r'服务(?:内容)?(?:是|为|[:：])(.+?)(?=[，,。；;]|项目(?:名称)?|供应商|对应方|公司|$)',residual)
 residual=re.sub(r'(?:项目(?:名称)?|服务(?:内容)?)(?:是|为|[:：])',' ',residual)
 q=compact(residual);groups=[];aliases=[]
 # Keep names' own grammar/service words intact when the whole remaining
 # fragment is grounded in a source project. Do not infer a company first.
 raw_project_fragment=core(q)
 protected_project=bool(len(raw_project_fragment)>=4 and not re.search(r'服务(?:内容)?(?:是|为|[:：])',query_content) and any(near_company(raw_project_fragment,core(v['project']))>=(.75 if len(raw_project_fragment)==4 else .8) for v in rows))
 for group,words in GROUPS.items():
  word=next((w for w in sorted(words,key=len,reverse=True) if w in q),None)
  if word:groups.append(group);aliases.append(word);q=q.replace(word,'')
 qualifiers=[s for s in ['新媒体','前期','补充协议','主合同'] if s in q or s in compact(query_content)]
 q=re.sub(r'合同名称|合同名|合作协议|补充协议|主合同|项目|服务|合同|协议|帮我|想找|我要|一下|请|那个|就是|这个|的|是|为','',q)
 q=core(q)
 if field_project:q=field_project+q
 if protected_project:q=raw_project_fragment;groups=[];aliases=[];qualifiers=[]
 # Infer a misspelled service suffix only when its project prefix is grounded.
 if not protected_project and not groups and q:
  options=[]
  for group,words in GROUPS.items():
   for word in words:
    if word in NEAR or not 2<=len(word)<=6:continue
    n=len(word)
    if len(q)<n+2:continue
    prefix,tail=q[:-n],q[-n:]
    if tail==word:continue
    if near_company(tail,word)>=(.75 if n==4 else .8) or (n==2 and sum(a==b for a,b in zip(tail,word))==1):
     if any(prefix in core(r['project']) for r in rows):options.append((n,group,prefix,tail,word))
  if options:
   options.sort(reverse=True);n,group,q,tail,word=options[0];groups=[group];aliases=[tail]
 # Infer a bare company fragment from the corpus, with the same bounded edit rule.
 if not protected_project and not supplier and not groups and q and len(q)>=4:
  company_names={company_core(r['supplier']) for r in rows}
  if any(near_company(q,name)>=(.75 if len(q)==4 else .8) for name in company_names):supplier=q;supplier_raw=q;q=''
 if not q and not supplier and not groups:return None
 exact_supplier=bool(supplier_raw and any(compact(supplier_raw)==compact(r['supplier']) for r in rows))
 hits=[]
 source_views=[core(v['project']+' '+v['title']) for v in rows];anchor_counts={}
 def rare_anchor(a):
  if a not in anchor_counts:anchor_counts[a]=sum(a in v for v in source_views)
  return anchor_counts[a]<=100
 for r in rows:
  if scope is not None and r['row'] not in scope:continue
  source=r['project']+' '+r['title'];pc,tc=core(r['project']),core(r['title'])
  if years and not years.issubset(set(re.findall(r'20\d{2}',source))):continue
  if editions and not editions.issubset(set(re.findall(r'第\d+届',compact(r['project'])))):continue
  if amount is not None:
   try:
    if Decimal(str(r['amount']).replace(',',''))!=amount:continue
   except InvalidOperation:continue
  if exact_supplier and compact(supplier_raw)!=compact(r['supplier']):continue
  cs=near_company(supplier,company_core(r['supplier'])) if supplier else 0.
  if supplier and cs<(.75 if len(supplier)==4 else .8):continue
  stem=re.sub(r'(?:文化传媒|文化传播|文化创意|影视广告|展览展示|传媒|科技|商务服务)$','',supplier or '')
  # Short company fragments are evaluated by actual ambiguity below.
  if supplier and '分公司' in supplier and '分公司' not in r['supplier']:continue
  if any(v not in compact(r['title']) for v in qualifiers if v not in ('主合同','补充协议')):continue
  if '补充协议' in qualifiers and '补充' not in r['title']:continue
  if '主合同' in qualifiers and '补充' in r['title']:continue
  if not all(any(w in compact(r['title']) for w in GROUPS[g] if w not in NEAR) for g in groups):continue
  ps,pl=match_fragment(q,pc);ts,tl=match_fragment(q,tc)
  grounded=max(ps,ts);longest=max(pl,tl)
  # A residual unknown project cannot be rescued merely by a generic service or company.
  project_near=bool(len(q)>=4 and (near_company(q,pc)>=(.75 if len(q)==4 else .8) or near_company(q,tc)>=(.75 if len(q)==4 else .8)))
  short_exact=bool(len(q)==2 and groups and q in pc and sum(q in core(v['project']) for v in rows)<=100)
  if q and not short_exact and not project_near and (grounded<.6 or len(q)<3 or (longest<3 and not(len(q)>=4 and grounded>=.75 and longest>=2 and (supplier or groups)))):continue
  if not q and not supplier and not (scope is not None or len(groups)>=1):continue
  if q and grounded<.75 and not project_near and not (supplier or groups):
   # A rare three-character anchor may rescue a longer phrase, but generic
   # two-character overlaps never retrieve a whole table. No project-specific map.
   anchors=[q[i:i+3] for i in range(len(q)-2) if q[i:i+3] in pc or q[i:i+3] in tc]
   rare=any(rare_anchor(a) for a in anchors)
   if len(q)<5 or not rare:continue
  # Restrict tolerated extra words to a small minority; no all-table generic fallback.
  if q and len(q)*(1-grounded)>4:continue
  reasons=[]
  if q:reasons.append('项目/合同片段覆盖'+str(round(grounded*100))+'%')
  if groups:reasons.append('服务证据：'+'、'.join(groups))
  if supplier:reasons.append('公司包含' if cs==1 else '公司一字近似，须核对源名')
  if years or editions:reasons.append('明确年份/届数相符')
  if amount is not None:reasons.append('金额相等')
  score=(grounded if q else 1)*.6+(cs if supplier else 1)*.25+.15
  hits.append(dict(r,score=round(score,4),requires_disambiguation=True,approximate_service=True,supplier_equal=bool(exact_supplier),match='；'.join(reasons)+'；仅候选，不确认身份',field_evidence={'project_title':round(grounded,3),'company':round(cs,3),'service_groups':groups,'unmatched_chars':round(len(q)*(1-grounded),2)}))
 if supplier and not q:
  stem=re.sub(r'(?:文化传媒|文化传播|文化创意|影视广告|展览展示|传媒|科技|商务服务)$','',supplier)
  names={compact(r['supplier']) for r in hits}
  if len(stem)<3 and len(names)>8:
   return dict(results=[],count=0,query_clarification=True,notice='公司简称近似主体过多，请补项目、更多公司字词或金额；没有自动认定公司。')
 hits.sort(key=lambda r:(-r['score'],r['row']))
 return dict(results=hits,count=len(hits),unique_exact=False,retrieval_method='field_evidence',query_filters={'project_fragment':q,'services':groups,'supplier_fragment':supplier,'years':sorted(years),'editions':sorted(editions),'amount':str(amount) if amount is not None else None},notice='按分字段证据给出近似候选；未知片段未当作精确条件，务必核对原始名称。' if hits else '可靠片段不足或明确条件冲突，未强行匹配。')
