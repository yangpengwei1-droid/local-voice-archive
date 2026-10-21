"""Deterministic ASR transcript routing. Produces proposals, never saves fields."""
import datetime
import re
import time
from app import search,norm,search_notice


def terminal_command(text,known_projects=()):
    clean=norm(text)
    phrases=[('停止监听','stop'),('关闭语音','stop'),('下一个','next')]
    if not any(phrase in clean for phrase,_ in phrases):return None
    if re.search(r'不|别|勿|未|禁止|合同|文件|标题|题目|写着|写的是|包含|文本|内容|字样|示例|举例|假设|如果|备注|引用|提及|合同名称|合同名|项目名称|项目名|名称|名字|叫|口令|命令|查询|搜索|查找|刚才说|提到|说过|说了|我说|意思',clean) or re.search(r'''[?？“”‘’"'「」『』]''',text):
        return 'clarify'
    for phrase,kind in phrases:
        if re.fullmatch(r'(?:好|好的|请)?'+phrase,clean):return kind
        if clean.endswith(phrase):
            prefix=clean[:-len(phrase)]
            projects=[norm(p) for p in known_projects if p] or ['合成项目']
            if not any(prefix.startswith(p) for p in projects):return 'clarify'
            # A deliberately narrow grammar preserves the observed ASR joined case.
            if not any(p in prefix for p,_ in phrases) and re.fullmatch(r'.+(?:项目|大会).*(?:服务|采购)供应商[是市].+(?:公司|中心|事务所|工厂|集团)',prefix):return kind
    return 'clarify'


def field_proposal(text,rules):
    if any(word in text for word in ('不是','不要','未通过','没通过','未结案通过','尚未','不确定','是否')):return dict(kind='clarification',message='否定或不确定字段指令需重新明确说明，不生成写入建议')
    changes={}
    for value in rules.get('G',[]):
        if value in text:changes['G']=value
    if len([v for v in rules.get('G',[]) if v in text])>1:return dict(kind='clarification',message='招采状态不唯一，请重说')
    for value in rules.get('V',[]):
        if value in text:changes['V']=value
    if len([v for v in rules.get('V',[]) if v in text])>1:return dict(kind='clarification',message='结案状态不唯一，请重说')
    dates=list(re.finditer(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?',text))
    if len(dates)>1:return dict(kind='clarification',message='日期不唯一，请一次说一个签订日期')
    date=dates[0] if dates else None
    if date:
        try:changes['X']=datetime.date(*map(int,date.groups())).isoformat()
        except ValueError:return dict(kind='clarification',message='日期无效，请说完整年月日')
    elif '签订' in text or '日期' in text:return dict(kind='clarification',message='请说明完整年份、月份和日期')
    if '结案报告' in text and 'V' not in changes:return dict(kind='clarification',message='有结案报告不代表结案通过，请明确审批结论')
    if not changes:return dict(kind='not_field_intent')
    return dict(kind='field_proposal',changes=changes,requires_manual_confirmation=True,closing_approval_required=changes.get('V')=='结案通过',message='仅生成待人工核对的字段建议，尚未保存')


class VoiceSession:
    def __init__(self,records):self.records=records;self.ids=None;self.base=None;self.stopped=False
    def route(self,text):
        command=terminal_command(text,[r.get('project','') for r in self.records])
        if command=='clarify':return dict(kind='clarification',message='检测到被提及、否定或不明确的控制词，未执行。需要操作时请单独说“下一个”或“停止监听”。',actions_saved=0)
        if command=='stop':
            self.stopped=True;return dict(kind='stop',actions_saved=0)
        if command=='next':
            self.ids=None;self.base=None;return dict(kind='next',actions_saved=0)
        if self.stopped:return dict(kind='ignored_after_stop',actions_saved=0)
        refine=bool(re.match(r'^(供应商|是|不是)',text))
        scope=self.base
        if refine and scope is None:return dict(kind='clarification',message='先说项目，再补供应商',actions_saved=0)
        start=time.perf_counter();rows=search(self.records,text,scope)
        self.ids=[r['row'] for r in rows]
        if not refine and rows:self.base=self.ids[:]
        notice=search_notice(text,rows)
        if notice:return dict(kind='clarification',message=notice,rows=[],context_ids=[],base_ids=self.base,actions_saved=0,search_ms=(time.perf_counter()-start)*1000)
        return dict(kind='candidates',rows=self.ids,search_ms=(time.perf_counter()-start)*1000,context_ids=self.ids,base_ids=self.base,actions_saved=0)
