"""Corpus based short-sentence retrieval. Read-only; no selection or write intent.

Known service vocabulary is shared across projects. Names and identities stay raw.
"""
import re,unicodedata
from decimal import Decimal,InvalidOperation
from difflib import SequenceMatcher

def compact(s):
 s=unicodedata.normalize('NFKC',str(s)).lower()
 digits={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'零':0,'两':2}
 def number(m):
  x=m[1]
  if x.isdigit():return '第'+str(int(x))+'届'
  if '十' in x:
   a,b=x.split('十');v=digits.get(a,1)*10+digits.get(b,0)
  else:v=digits.get(x,0)
  return '第'+str(v)+'届'
 s=re.sub(r'第([一二三四五六七八九十零两]+|\d+)届',number,s)
 return re.sub(r'[^\w]','',s)

# Business concepts, not project-specific exceptions. Near sounds are explicit,
# matching-only and reported. No arbitrary phonetic rewriting of company names.
GROUPS={
 '用车':('会务服务用车','会务用车','车辆租赁','车辆接送','车辆服务','用车服务','用车','车辆'),
 '航摄':('航摄','航拍','航设','航射','航社','行摄'),
 '视频制作':('视频制作剪辑','视频拍摄制作','视频制作','视频剪辑','宣传片制作'),
 '拍摄':('摄影摄像','视频拍摄','拍摄','摄影','摄像'),
 '搭建':('舞台搭建','舞美搭建','展览搭建','论坛搭建','搭建','搭台'),
 '氛围':('氛围营造','氛围制作','氛围布置','氛围','分为物料'),
 '物料':('物料制作','物料','物聊'), '票务':('机票代订','机票预订','机票待定','机票','票务'),
 '印刷':('印刷制作','印制','印刷'), '设计':('专项设计','设计'),
 '推广':('新媒体推广','新媒体宣传','宣传推广','宣传营销','营销推广','推广'),
 '保险':('保险',),'酒店':('酒店住宿','酒店','住宿'),
 '餐饮':('自助餐','餐饮','供餐','餐食'), '直播':('网络直播','直播'),
 '装修':('装修','装饰工程'), '施工':('工程施工','施工'),
 '测绘':('航拍测绘','测绘'), '策展':('总策展人','策展人','策展'),
 '执行':('活动执行','现场执行','执行'), '策划':('导演策划','策划'),
 '租赁':('设备租赁','租赁'),
}
NEAR={'物聊','航设','航射','航社','行摄','机票待定','分为物料'}

def core(s):
 s=compact(s)
 s=re.sub(r'20\d{2}(?:年(?!度))?|第\d+届','',s)
 s=re.sub(r'^(?:全国|中国|四川省|成都市|北京市|上海市|成都|北京|上海)','',s)
 return s.replace('项目','')

def company_core(s):
 return re.sub(r'(?:有限责任公司|股份有限公司|集团有限公司|有限公司|公司)$','',core(s))

def project_score(q,p):
 if not q:return 1.0
 if q in p:return 1.0
 # All substantial fragments must be grounded in the same source project.
 # A missing/extra character can produce a labelled approximate suggestion.
 blocks=SequenceMatcher(None,q,p,autojunk=False).get_matching_blocks()
 covered=sum(b.size for b in blocks if b.size>=2)
 return covered/max(1,len(q))

def strict_retrieve(rows,query,scope=None,related=False):
 raw=query
 query=re.sub(r'(20\d{2})年?(?:到|至)(20\d{2})年?',r'\1-\2',unicodedata.normalize('NFKC',query))
 q=compact(query)
 if '¥' in query or '￥' in query:return None
 if not q or re.search(r'不要|不是|不能|不确定|不查|别查|改为|写入|已询价|已比选|结案通过',q):return None
 # Preserve dedicated keyword/correction/number syntax handled by existing paths.
 if re.match(r'关键词|文件编号|归档编号|不是',q) or re.fullmatch(r'\d+',q):return None
 q=re.sub(r'^(?:请|帮我)?(?:找一下|查一下|查询|查找|查|找)','',q)
 q=re.sub(r'^项目(?:名称)?(?:是|为)','',q)
 editions=set(re.findall(r'第\d+届',q))
 amount_match=re.search(r'(?:金额(?:是|为)?|人民币)?(\d+(?:\.\d+)?)(万?元|万元|万)(?:整)?',unicodedata.normalize('NFKC',query).replace(',',''))
 # compact loses decimal dots, so parse and remove amount before compaction.
 if not amount_match:amount_match=re.search(r'金额(?:是|为)?\s*(\d+(?:\.\d+)?)(万)?',unicodedata.normalize('NFKC',query).replace(',',''))
 amount=None
 if amount_match:
  amount=Decimal(amount_match[1])*(10000 if '万' in (amount_match[2] or '') else 1)
  q=q.replace(compact(amount_match[0]),'')
 year_source=query.replace(amount_match[0],'') if amount_match else query
 years=set(re.findall(r'(?<!\d)20\d{2}(?!\d)',year_source))
 q=re.sub(r'20\d{2}(?:年(?!度))?|第\d+届','',q)
 supplier=None;supplier_full=None
 marker=re.search(r'(?:供应商|对应方|合作方|公司)(?:是|为)?(.+)$',q)
 if marker:
  supplier_full=marker[1]
  supplier=company_core(re.sub(r'(?:金额是|金额为|金额)$','',marker[1]));q=q[:marker.start()]
  if not supplier:return dict(results=[],count=0,notice='公司条件为空，请补充；未选择合同。')
 # A bare, sufficiently distinctive source company short name can refine scope.
 if not supplier:
  known={company_core(r['supplier']) for r in rows if company_core(r['supplier'])}
  if len(q)>=2 and any(q in s for s in known):supplier=q;q=''
 qualifiers=[v for v in ['新媒体','前期','补充协议','主合同'] if v in q]
 for v in ['补充协议','主合同']:q=q.replace(v,'')
 project_fragment=None
 service_query=core(q)
 # Ground the project prefix before reading service words. A project called
 # marketing/design must not impose those words on every contract title.
 for cut in range(len(service_query)-2,1,-1):
  prefix,tail=service_query[:cut],service_query[cut:]
  rest=tail
  for word in sorted({w for ws in GROUPS.values() for w in ws},key=len,reverse=True):rest=rest.replace(word,'')
  rest=re.sub(r'服务|合同|协议|的|是|为','',rest)
  if rest or not any(w in tail for ws in GROUPS.values() for w in ws):continue
  if len(prefix)<3:continue
  if any(prefix in core(r['project']) for r in rows):project_fragment=prefix;q=tail;break
 groups=[];aliases=[]
 for group,words in GROUPS.items():
  found=[w for w in sorted(words,key=len,reverse=True) if w in q]
  if found:
   alias=found[0];groups.append(group);aliases.append(alias);q=q.replace(alias,'')
 # Remove grammar and generic contract suffixes, never arbitrary content words.
 q=re.sub(r'^(?:服务(?:内容)?)(?:是|为)','',q)
 q=re.sub(r'合同|协议|项目|服务|的|是|为','',q)
 project_query=project_fragment or core(q)
 if not project_query and not groups and supplier is None and amount is None and not years and not editions:return None
 scoped=[r for r in rows if scope is None or r['row'] in scope]
 # A full source title is stronger than generic concepts; keep existing exact path.
 if any(compact(raw)==compact(r['title']) for r in scoped):return None
 def conditions(r):
  source=r['project']+' '+r['title'];c=compact(source)
  if years and not years.issubset(set(re.findall(r'20\d{2}',source))):return False
  if editions and not editions.issubset(set(re.findall(r'第\d+届',compact(r['project'])))):return False
  if supplier and supplier not in company_core(r['supplier']):return False
  exact_company=supplier_full and any(compact(supplier_full)==compact(v['supplier']) for v in rows)
  if exact_company and compact(supplier_full)!=compact(r['supplier']):return False
  if any(v not in c for v in qualifiers if v not in ('主合同','补充协议')):return False
  if '补充协议' in qualifiers and '补充' not in r['title']:return False
  if '主合同' in qualifiers and '补充' in r['title']:return False
  if '分公司' in (supplier or '') and '分公司' not in r['supplier']:return False
  if amount is not None:
   try:
    if Decimal(str(r['amount']).replace(',',''))!=amount:return False
   except InvalidOperation:return False
  return related or all(any(w in compact(r['title']) for w in GROUPS[g] if w not in NEAR) for g in groups)
 hits=[]
 for r in scoped:
  if not conditions(r):continue
  score=project_score(project_query,core(r['project']))
  if related:score=max(score,project_score(project_query,core(r['title'])))
  if score<.75 and project_query:
   # Corpus-derived segmentation supports unknown service names too. Every
   # fragment is grounded in the same row, without a per-project alias table.
   pc,tc=core(r['project']),core(r['title'])
   if project_query in tc:score=1.0
   for cut in range(2,len(project_query)-1):
    if project_query[:cut] in pc and project_query[cut:] in tc:score=max(score,.99)
  if score < (.5 if related else .75) or (project_query and len(project_query)<2):continue
  # Require a meaningful contiguous anchor for fuzzy or shortened project names.
  if project_query and score<.99 and max((b.size for field in (('project','title') if related else ('project',)) for b in SequenceMatcher(None,project_query,core(r[field])).get_matching_blocks()),default=0)<3:continue
  reason=[]
  if project_query:reason.append('项目简称包含' if project_query in core(r['project']) else '项目/合同片段近似')
  if groups:reason.append('服务同义：'+'、'.join(groups))
  if supplier:reason.append('公司简称包含（非身份确认）')
  if any(a in NEAR for a in aliases):reason.append('有限近音候选，原转写未改')
  if amount is not None:reason.append('金额相等')
  if years or editions:reason.append('保留明确年份/届数')
  hits.append(dict(r,supplier_equal=bool(supplier_full and compact(supplier_full)==compact(r['supplier'])),branch_requires_clarification=bool(supplier and '分公司' in supplier and compact(supplier_full or supplier)!=compact(r['supplier'])),score=round(score,3),requires_disambiguation=True,approximate_service=score<1 or any(a in NEAR for a in aliases),match='；'.join(reason) or '条件候选，需人工核对'))
 # Rank exact fragments first; keep bounded approximate projects rather than
 # silently dropping them just because another project has a literal substring.
 hits.sort(key=lambda r:(-r['score'],r['row']))
 # Bounded approximation must never rescue an unknown project through service alone.
 return dict(results=hits,count=len(hits),unique_exact=False,suggestions=[],low_confidence=not bool(hits),retrieval_method='short_sentence',query_filters=dict(project_fragment=project_query,services=groups,supplier_fragment=supplier,amount=str(amount) if amount is not None else None,years=sorted(years),editions=sorted(editions)),matching_only_alias={a:g for a,g in zip(aliases,groups)},notice=('候选较多，请继续补充年份、届数、公司或金额。' if len(hits)>12 else '')+'仅为可解释候选；请核对源名称、年份、届数、公司及主补合同后人工选择。' if hits else '没有满足项目片段及明确条件的候选；未仅凭通用服务词匹配其他项目。')


def retrieve(rows,query,scope=None):
 strict=strict_retrieve(rows,query,scope)
 # Service concepts inferred from unmarked speech are weaker than a grounded
 # project anchor. Never silently drop them: expose a separate related lane.
 # Identity/year/edition/amount/main-vs-supplement conditions remain mandatory.
 if strict and not strict.get('count'):
  filters=strict.get('query_filters',{})
  if filters.get('services') and len(filters.get('project_fragment') or '')>=3:
   nearby=strict_retrieve(rows,query,scope,related=True)
   if nearby and nearby.get('count'):
    related_rows=[]
    for row in nearby['results']:
     missing=[g for g in filters['services'] if not any(w in compact(row['title']) for w in GROUPS[g] if w not in NEAR)]
     related_rows.append(dict(row,related_candidate=True,unmatched_services=missing,requires_disambiguation=True,match='相关候选（非精确命中）；'+('未匹配服务：'+'、'.join(missing) if missing else '服务词相符；项目/合同片段仍需核对')))
    strict=dict(strict,related_results=related_rows,related_count=len(related_rows),notice='严格条件未匹配；另列当前范围内相关候选。原查询服务条件：'+'、'.join(filters['services'])+'。请核对原合同；未忽略条件作精确命中，未自动选择或扩展范围。')
 if re.match(r'关键词|文件编号|归档编号',compact(query)) or re.fullmatch(r'\d+',compact(query)) or '¥' in query or '￥' in query:return strict
 if any(compact(query)==compact(r[k]) for r in rows for k in ('title','number','contract') if r.get(k)):return strict
 if strict and strict.get('count'):
  # An incomplete company fragment can contain one name and be one edit from
  # another. Keep both as labelled candidates; a full known legal name wins.
  marker=re.fullmatch(r'(?:供应商|对应方|合作方|公司)(?:是|为|[:：])?(.+)',query.strip())
  supplier_fragment=strict.get('query_filters',{}).get('supplier_fragment')
  if scope is None and supplier_fragment and len(supplier_fragment)<=2 and len({compact(r['supplier']) for r in strict['results']})>8:
   return dict(results=[],count=0,unique_exact=False,query_clarification=True,notice='公司片段过短且对应多个主体，请补项目或更多公司字词；未扩展为全表候选。')
  company_text=marker[1].strip() if marker else query.strip()
  known_full=any(compact(company_text)==compact(r['supplier']) for r in rows)
  if supplier_fragment and not known_full and not re.search(r'有限公司|有限责任公司|分公司$',company_text):
   from r3_evidence import evidence_retrieve
   nearby=evidence_retrieve(rows,query,scope)
   if nearby and nearby.get('count'):
    combined={r['row']:r for r in nearby['results']}
    combined.update({r['row']:r for r in strict['results']})
    strict=dict(strict,results=sorted(combined.values(),key=lambda r:(-r.get('score',0),r['row'])),count=len(combined),unique_exact=False,notice='公司片段含包含匹配和一字近似候选；请核对真实全名，不自动认定主体。')
  return strict
 if strict and strict.get('query_clarification'):return strict
 from r3_evidence import evidence_retrieve
 fallback=evidence_retrieve(rows,query,scope)
 if fallback and (fallback.get('count') or fallback.get('query_clarification')):return fallback
 if strict and strict.get('related_count'):return strict
 from r3_partial import partial_retrieve
 partial=partial_retrieve(rows,query,scope)
 return partial or (strict if strict is not None else fallback)
