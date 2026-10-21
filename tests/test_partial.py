"""All names, anniversaries and amounts below are fictional."""
import unittest
from r3_partial import partial_retrieve
from r3_retrieval import retrieve

def row(i,p,t,s='甲公司',a='100'):return dict(row=i,project=p,title=t,supplier=s,amount=a,contract='',number='')
class Partial(unittest.TestCase):
 def setUp(self):self.rows=[row(1,'2025第十届山川大会','餐品供应协议','甲公司'),row(2,'2025第十届山川大会','舞台租赁','供应链公司'),row(3,'2024第九届其他大会','山谷治理'),row(4,'77周年文艺活动','安保服务'),row(5,'770周年另一活动','安保服务'),row(6,'2025第十届山川大会','餐品供应补充协议')]
 def ids(self,q,scope=None):return {r['row'] for r in (partial_retrieve(self.rows,q,scope) or {}).get('related_results',[])}
 def test_partial_fields_without_project_anchor(self):
  d=partial_retrieve(self.rows,'错词供应');self.assertEqual({r['row'] for r in d['related_results']},{1,2,6});self.assertEqual(d['count'],0);self.assertFalse(d['unique_exact'])
  self.assertTrue(all(r['unmatched_fragments']==['错词'] for r in d['related_results']))
  self.assertEqual(self.ids('错词山川'),{1,2,6});self.assertEqual(self.ids('对应方错词供应'),{2});self.assertEqual(self.ids('错词租赁'),{2})
 def test_scope_and_hard_constraints(self):
  self.assertEqual(self.ids('错词供应',[1]),{1});self.assertEqual(self.ids('错词供应',[4]),set())
  for q in ['2023错词供应','第八届错词供应','错词供应金额200元','错词供应对应方甲公司金额200元','错词安保金额100元第九届']:
   self.assertEqual(self.ids(q),set(),q)
  self.assertEqual(self.ids('2025第十届错词供应主合同'),{1,2});self.assertEqual(self.ids('2025错词供应补充协议'),{6})
  self.assertEqual(self.ids('77周年错词安保'),{4})
  self.assertEqual(self.ids('错词供应对应方甲公司'),{1,6})
 def test_no_identifier_no_rescue(self):
  for q in ['合同服务公司项目','玄妙火星','不要供应','文件编号2099001','回退上一句','已询价','2025','金额100元']:
   self.assertEqual(self.ids(q),set(),q)
 def test_existing_strict_stays_first(self):
  d=retrieve(self.rows,'餐品供应');self.assertNotIn('related_results',d);self.assertTrue(d['count'])
if __name__=='__main__':unittest.main()
