"""Compare, coding-task and agentic-task data (lab_app/compare.py) and the coding/agentic job kinds."""
import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lab_app'))
import compare as C
import actions


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patch = [patch.object(C, 'RESULTS', root / 'results'), patch.object(C, 'ROOT', root)]
        for p in self.patch:
            p.start()
        spec = {'styles': {'normal': ''}, 'prompts': [
            {'id': 'code-add', 'kind': 'code', 'prompt': 'Write add(a, b).', 'tests': 'assert add(1, 2) == 3'},
            {'id': 'code-sub', 'kind': 'code', 'prompt': 'Write sub(a, b).', 'tests': 'assert sub(3, 1) == 2'}]}
        write(root / 'benches' / 'coding-v1.json', spec)
        # Two folders for the same model: matched through meta.json, not the folder name.
        for name, model in (('coding-v1-a', 'qwen:27b'), ('odd-name', 'gemma:26b')):
            d = root / 'results' / f'glm-bench-{name}'
            write(d / 'meta.json', {'ollama_model': model, 'prompts': 'benches/coding-v1.json', 'started': '2026-11-14T00:00:00Z'})
            write(d / 'code-add__normal__exact.json', {'id': 'code-add', 'kind': 'code', 'style': 'normal', 'config': 'exact', 'correct': True,
                                                       'answer_s': 2.0, 'text': '```python\ndef add(a, b):\n    return a + b\n```'})
            wrong = model == 'gemma:26b'
            write(d / 'code-sub__normal__exact.json', {'id': 'code-sub', 'kind': 'code', 'style': 'normal', 'config': 'exact', 'correct': not wrong,
                                                       'answer_s': 1.0 if wrong else 4.0,
                                                       'text': '```python\ndef sub(a, b):\n    return a + b\n```' if wrong else '```python\ndef sub(a, b):\n    return a - b\n```'})
        a = root / 'results' / 'agentic-v1-qwen-off'
        write(a / 'meta.json', {'model': 'ollama/qwen:27b', 'thinking': 'off', 'started': '2026-11-14T01:00:00Z'})
        write(a / 'bugfix-inventory' / 'result.json', {'solved': True, 'hidden_passed': 4, 'hidden_total': 4, 'wall_s': 60, 'timed_out': False})
        (a / 'bugfix-inventory' / 'pi.jsonl').write_text('\n'.join(json.dumps(e) for e in [
            {'type': 'message_end', 'message': {'role': 'assistant', 'timestamp': 1000, 'content': [{'type': 'toolCall', 'id': 'x', 'name': 'bash', 'arguments': {'command': 'ls'}}]}},
            {'type': 'message_end', 'message': {'role': 'assistant', 'timestamp': 6000, 'content': [{'type': 'toolCall', 'id': 'guard_1', 'name': 'read', 'arguments': {'path': 'a.py'}}]}}]))
        (a / 'bugfix-inventory' / 'hidden.txt').write_text('test_a (x.Hidden.test_a) ... ok\ntest_b (x.Hidden.test_b) ... FAIL\n')

    def tearDown(self):
        for p in self.patch:
            p.stop()
        self.tmp.cleanup()

    def test_scorecard_rows_and_models(self):
        self.assertEqual([m['model'] for m in C.models()], ['gemma:26b', 'qwen:27b'])
        r = C.compare(['qwen:27b', 'gemma:26b'])
        coding = next(x for x in r['rows'] if x['family'] == 'coding')
        self.assertEqual((coding['cells']['qwen:27b']['off']['solved'], coding['cells']['gemma:26b']['off']['solved']), (2, 1))
        self.assertEqual(coding['cells']['gemma:26b']['off']['bench'], 'odd-name')
        self.assertEqual(coding['cells']['qwen:27b']['off']['answer_s'], 3.0)
        agentic = next(x for x in r['rows'] if x['family'] == 'agentic')
        self.assertEqual(agentic['cells']['qwen:27b']['off']['solved'], 1)
        self.assertIsNone(agentic['cells']['gemma:26b']['off'])

    def test_failure_reason_runs_the_code(self):
        t = C.coding_task('odd-name', 'code-sub')
        a = t['answers'][0]
        self.assertEqual((a['reason'], a['detail'][0]), ('failed-test', 'assert sub(3, 1) == 2'))
        self.assertEqual((t['index'], t['count'], t['prev'], t['next']), (2, 2, 'code-add', None))
        self.assertEqual(C.coding_failures('odd-name')['failures'][0]['id'], 'code-sub')
        with self.assertRaises(KeyError):
            C.coding_task('../etc', 'x')

    def test_agentic_task_steps_and_tests(self):
        d = C.agentic_task('qwen-off', 'bugfix-inventory')
        self.assertEqual([(s['tool'], s['repaired'], s['t']) for s in d['steps']], [('bash', False, 0), ('read', True, 5)])
        self.assertEqual(d['hidden_tests'], [{'name': 'test_a', 'ok': True}, {'name': 'test_b', 'ok': False}])
        self.assertEqual(C.agentic_list()['runs'][0]['solved'], 1)


class JobKindTests(unittest.TestCase):
    def test_coding_and_agentic_commands_are_validated(self):
        with patch.object(actions, 'ollama_models', return_value=['qwen3.8:27b-mlx']):
            argv, title = actions.build_command('coding', {'ollama_model': 'qwen3.8:27b-mlx', 'configs': ['exact', 'native-long']})
            self.assertIn('coding-v1-qwen3-8-27b-mlx', argv)
            self.assertIn('benches/coding-v1.json', argv)
            argv, _ = actions.build_command('agentic', {'ollama_model': 'qwen3.8:27b-mlx', 'thinking': 'medium'})
            self.assertEqual(argv[argv.index('--name') + 1], 'qwen3-8-27b-mlx-medium')
            for bad in ({'ollama_model': 'other', 'configs': ['exact']}, {'ollama_model': 'qwen3.8:27b-mlx', 'configs': ['d030']}):
                with self.assertRaises(actions.ActionError):
                    actions.build_command('coding', bad)
            with self.assertRaises(actions.ActionError):
                actions.build_command('agentic', {'ollama_model': 'qwen3.8:27b-mlx', 'thinking': 'rm -rf'})
            with patch.object(actions, 'GUARD_EXTENSION', Path('/nonexistent/index.ts')), self.assertRaises(actions.ActionError):
                actions.build_command('agentic', {'ollama_model': 'qwen3.8:27b-mlx', 'thinking': 'off', 'guard': True})
        self.assertIn('coding', actions.GPU_KINDS)
        self.assertIn('agentic', actions.GPU_KINDS)


if __name__ == '__main__':
    unittest.main()
