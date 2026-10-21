import unittest
from r3_retrieval import retrieve

def row(i,project,title,supplier='甲公司',amount='100'):
 return dict(row=i,project=project,title=title,supplier=supplier,amount=amount,contract='',number='')
class Related(unittest.TestCase):
 def setUp(self):
  self.rows=[row(1,'2025第十届山海文化大会','推广执行协议'),row(2,'2025第十届山海文化大会','搭建合同'),row(3,'2024第九届山海文化大会','推广合同'),row(4,'2025第十届山海文化大会','推广补充协议')]
 def test_generic_service(self):
  for word in ['航摄','租赁','用车','视频制作']:
   d=retrieve(self.rows,'2025第十届山海文化大会'+word)
   self.assertEqual(d['count'],0);self.assertEqual({r['row'] for r in d['related_results']},{1,2,4});self.assertFalse(d['unique_exact'])
   self.assertTrue(all(r['unmatched_services'] for r in d['related_results']))
 def test_strict_service_wins(self):
  d=retrieve(self.rows,'山海文化大会搭建');self.assertEqual([r['row'] for r in d['results']],[2]);self.assertNotIn('related_results',d)
 def test_strong_conditions_and_scope(self):
  for q in ['2023山海文化大会航摄','第八届山海文化大会航摄','山海文化大会航摄金额200元','山海文化大会航摄供应商乙公司','不存在天地论坛航摄']:
   d=retrieve(self.rows,q);self.assertFalse(d.get('related_results'));self.assertEqual(d['count'],0)
  d=retrieve(self.rows,'2025山海文化大会主合同航摄',[1,4]);self.assertEqual([r['row'] for r in d['related_results']],[1])
 def test_service_only_never_widens(self):
  d=retrieve(self.rows,'航摄',[1]);self.assertFalse(d.get('related_results'));self.assertEqual(d['count'],0)
if __name__=='__main__':unittest.main()
