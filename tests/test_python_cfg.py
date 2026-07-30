import unittest

from agent.security_agent.utils.python_cfg_generator import PythonCFGGenerator
from agent.security_agent.models import CodeDiff


class PythonCFGGeneratorTests(unittest.TestCase):
    def test_generate_from_source_creates_function_cfg(self):
        source = '''
def login(username, password):
    if username == "admin":
        return True
    return False
'''
        gen = PythonCFGGenerator()
        cfgs = gen.generate_from_source(source, file_name="auth.py")

        self.assertEqual(len(cfgs), 1)
        cfg = cfgs[0]
        self.assertEqual(cfg.function_name, "login")
        self.assertTrue(any(n.is_entry for n in cfg.nodes))
        self.assertTrue(any(n.is_conditional for n in cfg.nodes))

    def test_generate_from_diff_filters_changed_functions(self):
        source = '''
def login(username, password):
    return username == password

def logout():
    pass
'''
        diff = CodeDiff(
            file_path="auth.py",
            additions=[{"line": 2, "content": "def login(username, password):"}],
            deletions=[],
            raw_diff="",
        )
        gen = PythonCFGGenerator()
        cfgs = gen.generate_from_diff([diff], repository_path=".")

        # 因为 repository_path 下没有 auth.py，所以返回空
        self.assertEqual(cfgs, [])

    def test_generate_from_source_with_function_filter(self):
        source = '''
def login():
    pass

def logout():
    pass
'''
        gen = PythonCFGGenerator()
        cfgs = gen.generate_from_source(source, file_name="auth.py", functions=["login"])

        self.assertEqual(len(cfgs), 1)
        self.assertEqual(cfgs[0].function_name, "login")


if __name__ == "__main__":
    unittest.main()
