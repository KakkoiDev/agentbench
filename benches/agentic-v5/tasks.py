"""agentic-v5: the hardest Python exercises of Aider's polyglot benchmark, same format and grading as agentic-v1..v4.

Written on 2026-11-22 because the best local models solve 14/15 of v1..v3 and 47-50 of v4's 52 hidden
tests. These 15 tasks come from Aider's polyglot benchmark (github.com/Aider-AI/polyglot-benchmark), the
Exercism exercises Aider picked as the hardest for frontier models; Exercism content is MIT-licensed
(see SOURCES.md). The exercise files are kept unchanged in benches/agentic-v5/exercises/<name>/.

Each task: the agent gets the exercise's own instructions, the stub and a visible test file with only
2-3 of the official tests. The hidden tests are the full official test file (keeping the official test
names, which the site shows as what a model managed and missed), so the score is partial credit (hidden
tests passed). Unlike Aider's benchmark, the model never sees the full tests: exact error messages, edge
cases and output formats must come from the instructions alone. The reference fix is the exercise's
.meta/example.py.

    .venv/bin/python benches/agentic-v5/tasks.py     # validate every task
"""
import ast
import importlib.util
import re
import sys
from pathlib import Path

_v1 = importlib.util.spec_from_file_location('agentic_v1_tasks', Path(__file__).resolve().parents[1] / 'agentic-v1' / 'tasks.py')
V1 = importlib.util.module_from_spec(_v1)
_v1.loader.exec_module(V1)
write_files, run_hidden, run_visible = V1.write_files, V1.run_hidden, V1.run_visible

EXERCISES = Path(__file__).resolve().parent / 'exercises'

# exercise -> (what makes it hard, the official tests the agent can see)
PICKS = {
    'forth': ('a stack-language interpreter: user-defined words bound at definition time, overriding built-ins, case-insensitivity, exact error types and messages',
              ['test_parsing_and_numbers_numbers_just_get_pushed_onto_the_stack', 'test_addition_can_add_two_numbers',
               'test_user_defined_words_can_consist_of_built_in_words']),
    'poker': ('ranking poker hands: ace-low straights, kickers, ties across multiple decks',
              ['test_single_hand_always_wins', 'test_one_pair_beats_high_card', 'test_a_tie_has_multiple_winners']),
    'bowling': ('a bowling score keeper: tenth-frame bonus rolls and every invalid-roll and incomplete-game error',
                ['test_should_be_able_to_score_a_game_with_all_zeros', 'test_should_be_able_to_score_a_game_with_no_strikes_or_spares',
                 'test_a_spare_followed_by_zeros_is_worth_ten_points']),
    'sgf-parsing': ('a parser for the SGF game-record format: escapes, whitespace rules, nested variations and exact errors',
                    ['test_single_node_tree', 'test_two_nodes', 'test_multiple_property_values']),
    'grep': ('a grep clone: six flags that combine, file-name and line-number prefixes, several files',
             ['test_one_file_one_match_no_flags', 'test_one_file_one_match_print_line_numbers_flag',
              'test_one_file_one_match_case_insensitive_flag']),
    'wordy': ('word problems evaluated left to right, rejecting each kind of malformed question with the right message',
              ['test_just_a_number', 'test_addition', 'test_multiplication']),
    'book-store': ('the cheapest grouping of discounted books, where greedy grouping is wrong',
                   ['test_only_a_single_book', 'test_two_different_books', 'test_five_different_books']),
    'react': ('a reactive spreadsheet: compute cells that update in order and fire callbacks only on real changes',
              ['test_input_cells_have_a_value', 'test_an_input_cell_s_value_can_be_set', 'test_compute_cells_calculate_initial_value']),
    'pov': ('re-rooting a tree from another node\'s point of view and finding paths between nodes',
            ['test_results_in_the_same_tree_if_the_input_tree_is_a_singleton', 'test_can_reroot_a_tree_with_a_parent_and_one_sibling',
             'test_can_find_path_to_parent']),
    'zipper': ('a functional zipper over a binary tree: immutable moves, edits and rebuilding the whole tree',
               ['test_data_is_retained', 'test_left_right_and_value', 'test_set_value']),
    'dominoes': ('chaining dominoes into a loop with backtracking, rejecting disconnected sets',
                 ['test_empty_input_empty_output', 'test_singleton_input_singleton_output', 'test_three_elements']),
    'go-counting': ('flood-filling Go territories and their owners, with coordinate validation',
                    ['test_black_corner_territory_on_5x5_board', 'test_white_center_territory_on_5x5_board',
                     'test_one_territory_is_the_whole_board']),
    'two-bucket': ('the water-jug puzzle with its forbidden state, move counting and impossible goals',
                   ['test_measure_using_bucket_one_of_size_3_and_bucket_two_of_size_5_start_with_bucket_one',
                    'test_measure_using_bucket_one_of_size_3_and_bucket_two_of_size_5_start_with_bucket_two']),
    'rest-api': ('an IOU REST API over JSON strings: debts that net out between lenders and borrowers',
                 ['test_no_users', 'test_add_user']),
    'transpose': ('transposing ragged text with the exact left-padding rule and no trailing padding',
                  ['test_empty_string', 'test_two_characters_in_a_row', 'test_simple']),
}


def _instructions(ex, module):
    docs = [ex / '.docs' / f for f in ('introduction.md', 'instructions.md', 'instructions.append.md')]
    text = '\n\n'.join(p.read_text().strip() for p in docs if p.exists())
    return text + f"\n\nImplement it in `{module}.py`. Make sure `python3 -m unittest` passes."


def _visible(test_src, keep):
    """The official test file with every test method removed except those in keep (helpers stay)."""
    tree = ast.parse(test_src)
    lines = test_src.splitlines(keepends=True)
    drop, found = [], set()
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith('test_'):
                if fn.name in keep:
                    found.add(fn.name)
                else:
                    start = min([fn.lineno] + [d.lineno for d in fn.decorator_list]) - 1
                    drop.append((start, fn.end_lineno))
    missing = set(keep) - found
    assert not missing, f'visible tests not found: {missing}'
    for start, end in sorted(drop, reverse=True):
        # also drop the blank lines and section comments just above the removed test
        while start > 0 and (not lines[start - 1].strip() or lines[start - 1].startswith('    #')):
            start -= 1
        del lines[start:end]
    text = re.sub(r'\n{3,}(?=    )', '\n\n', ''.join(lines))
    return re.sub(r'\n{4,}', '\n\n\n', text)


TASKS = {}
for _name, (_area, _keep) in PICKS.items():
    _ex = EXERCISES / _name
    _mod = _name.replace('-', '_')
    _tests = (_ex / f'{_mod}_test.py').read_text()
    _ref = (_ex / '.meta' / 'example.py').read_text()
    TASKS[_name] = dict(
        area=_area,
        source=f'Aider polyglot benchmark, python/exercises/practice/{_name} (Exercism, MIT)',
        instruction=_instructions(_ex, _mod),
        files={f'{_mod}.py': (_ex / f'{_mod}.py').read_text(), f'test_{_mod}.py': _visible(_tests, _keep)},
        hidden=_tests,
        fix={f'{_mod}.py': (lambda ref: lambda _old: ref)(_ref)},
    )


def _validate():
    import tempfile
    bad = 0
    for name, task in TASKS.items():
        with tempfile.TemporaryDirectory() as start, tempfile.TemporaryDirectory() as fixed:
            write_files(task, start)
            passed, total, _ = run_hidden(task, start)
            write_files(task, fixed)
            for rel, change in task['fix'].items():
                p = Path(fixed, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(change(p.read_text() if p.exists() else ''))
            fpassed, ftotal, out = run_hidden(task, fixed)
            vis_ok, vis_out = run_visible(fixed)
            m = re.search(r'Ran (\d+) test', vis_out)
            nvis = int(m[1]) if m else 0
            vis_ok = vis_ok and nvis == len(PICKS[name][1])
            ok = ftotal and fpassed == ftotal and 2 * passed < (total or 1) and vis_ok
            print(f"{'ok  ' if ok else 'FAIL'} {name}: start {passed}/{total}, reference fix {fpassed}/{ftotal}, "
                  f"visible tests ({nvis}) with fix {'pass' if vis_ok else 'FAIL'}")
            if not ok:
                bad += 1
                print(out[-2500:], vis_out[-800:])
    print(f"{len(TASKS) - bad}/{len(TASKS)} tasks valid, {sum(t['hidden'].count('    def test_') for t in TASKS.values())} hidden tests")
    return bad


if __name__ == '__main__':
    sys.exit(1 if _validate() else 0)
