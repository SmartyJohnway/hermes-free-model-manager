import ast, json, subprocess, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
class ProductRegression(unittest.TestCase):
    def test_syntax(self):
        files=list((ROOT/"src").glob("*.py"))+list((ROOT/"scripts").glob("*.py"))
        self.assertGreater(len(files),30)
        for p in files:
            with self.subTest(p=p.name): ast.parse(p.read_text(encoding="utf-8"))
    def test_imports(self):
        for name in ("manager_ui","manager_ai_exchange","configuration_preview","configuration_operations","tool_evidence_policy","eligibility_catalog","provider_catalog"):
            with self.subTest(name=name): __import__(name)
    def test_ai_contract(self):
        import manager_ai_exchange as ai
        self.assertEqual(ai.REQ_SCHEMA,"hermes-free-manager-ai-request.v1")
        self.assertEqual(ai.REC_SCHEMA,"hermes-free-manager-ai-recommendation.v1")
        self.assertTrue(ai.SOURCE_PATH.is_file())
        self.assertTrue(ai.GUIDE_PATH.is_file())
    def test_catalog_snapshot(self):
        d=json.loads((ROOT/"reports"/"eligibility_catalog.json").read_text(encoding="utf-8"))
        self.assertIsInstance(d["providers"], list)
    def test_cli(self):
        for action in ("status","catalog","restore-info"):
            with self.subTest(action=action):
                r=subprocess.run([sys.executable,"-B",str(ROOT/"scripts"/"manager_cli.py"),action],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=40)
                self.assertEqual(r.returncode,0,r.stderr[-500:])
    def test_gui_smoke(self):
        r=subprocess.run([sys.executable,"-B",str(ROOT/"src"/"manager_ui.py"),"--self-test"],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=40)
        self.assertEqual(r.returncode,0,r.stderr[-500:])
        self.assertTrue("MANAGER_AI_UI_SMOKE_OK" in r.stdout or "MANAGER_EMPTY_CATALOG_OK" in r.stdout)
if __name__=="__main__": unittest.main(verbosity=2)
