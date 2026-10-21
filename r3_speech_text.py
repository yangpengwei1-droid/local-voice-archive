"""Conservative matching-only removal of spoken fillers; raw text stays intact."""
import re

FILLER=r'(?:[嗯呃额啊唔]+|那个|就是)'

def matching_text(text,entities=()):
    if not re.search(r'[嗯呃额啊唔]|那个|就是',text):return text.strip()
    protected={}
    # Exact known names win over filler removal, including names containing 那个/就是.
    for entity in sorted(set(entities),key=len,reverse=True):
        if entity and entity in text:
            token=f'\ue000{len(protected)}\ue001';text=text.replace(entity,token);protected[token]=entity
    parts=re.split(r'[，,。；;！!\s]+',text.strip())
    parts=[p for p in parts if p and not re.fullmatch(FILLER,p)]
    text='，'.join(parts)
    # Leading hesitations can be unpunctuated. Lexical fillers require a clear
    # instruction prefix; never remove them from the middle of an unknown name.
    text=re.sub(r'^[嗯呃额啊唔]+','',text)
    text=re.sub(r'^(?:(?:那个|就是))+(?=\ue000|已|招采|采购状态|结案|日期|签订|合同签订|分配|生成|登记|文件编号|归档编号|确认|不要|不是|不能|项目(?:名称)?是|服务是|供应商是|下一个合同)','',text)
    for token,entity in protected.items():text=text.replace(token,entity)
    return text.strip('，')
