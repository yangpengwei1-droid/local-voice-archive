"""U 返还情况: exact, distinct business states; empty never means no."""
import re

ALIASES={
    '合同已返回':'已返还合同','合同已经返回':'已返还合同',
    '合同已返还':'已返还合同','合同已经返还':'已返还合同',
    '已返还合同':'已返还合同','已返回':'已返还合同','已返还':'已返还合同','已经返回':'已返还合同','已经返还':'已返还合同',
    '已返还合同未签日期':'已返还合同未签日期',
    '合同已返回未签日期':'已返还合同未签日期',
    '合同已返回但未签日期':'已返还合同未签日期',
    '已返还日期未签字':'已返还日期未签字',
    '合同已返回日期未签字':'已返还日期未签字',
    '合同已返回但日期未签字':'已返还日期未签字',
    '已被取走':'已被取走','合同已被取走':'已被取走',
}

def value(text):
    text=re.sub(r'[，,。！!\s]','',text)
    text=re.sub(r'^返还情况(?:改为|改成|设为|是)','',text)
    return ALIASES.get(text)

def proposal(text,choices,current):
    if not re.search('返还|返回|取走',text):return None
    if re.search(r'不要|不能|别|不确定|是否|能否|吗|未收到|没收到|未返|没返|未回|没回|没有|尚未|还没|[？?]',text):return dict(kind='clarification',message='返还情况仅核对，不修改。当前值：'+(current or '未填写'))
    correction=re.fullmatch(r'不是(.+?)[，, ]*(?:而是|是)(.+?)[。！!]?$',text)
    if correction:
        old,new=correction.groups();target=value(new)
        if value(old) not in choices and old.strip('，,。！! ')!=current:target=None
    else:target=value(text)
    if not target:return dict(kind='clarification',message='返还状态不明确，未保存。请区分未签日期、日期未签字、已被取走；空白不代表未返回。')
    if target not in choices:return dict(kind='clarification',message='该行U没有支持此值的有效下拉，禁止修改；源值保持原样。')
    return dict(kind='field_proposal',changes={'U':target})
