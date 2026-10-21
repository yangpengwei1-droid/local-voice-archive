"""All projects and companies here are intentionally fictional."""
from pathlib import Path
from tools.workbook import build

def demo_rows():
 rows=[]
 for i,title in enumerate(['机场安保服务合同','酒店驻地安保服务合同','体育中心安保服务合同','体育中心安保服务补充协议','车辆租赁合同'],1):
  rows.append(dict(registered='2030-01-01',contract=f'FICTION-STAR-{i:03}',number='2030001' if i==2 else '',project='第五届星桥创意节（虚构）',title=title,amount=str(i*1000),supplier=f'虚构星桥服务企业{i}号'))
 rows.append(dict(registered='2030-01-01',contract='FICTION-SUPPLY-001',project='虚构谷物供应项目',title='谷物购销合同',amount='据实结算',supplier='虚构北辰农业企业'))
 rows.append(dict(registered='2030-01-01',contract='FICTION-EXPO-001',project='第一届北辰科幻展（虚构）',title='舞台搭建合同',amount='2000',supplier='虚构北辰舞台企业'))
 rows.append(dict(registered='2030-01-01',contract='FICTION-OTHER-001',project='虚构山谷露营节',title='会场布置合同',amount='3000',supplier='虚构山谷文化企业'))
 return rows
if __name__=='__main__':
 p=Path(__file__).resolve().parents[1]/'examples'/'demo.xlsx'
 with p.open('xb') as f:f.write(build(demo_rows()))
 print('Created fictional example:',p.name)
