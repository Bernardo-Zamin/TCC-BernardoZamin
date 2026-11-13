"""Campaign grid, episode file names, resume and worker split."""

import tempfile
import unittest
from pathlib import Path

from harness.campaign import (ATTACKS, BLOCKED, CAMPAIGN_MODELS, CHUNK_HOURS, EPISODES_PER_REP,
                              GRID, INVALID, TARGET_REPS, WON, Key, chunks, done_keys,
                              episode_path, episode_paths, file_name, name_pattern,
                              parse_name, remove_previous, split_chunks, subfolder)
from harness.pricing import MODELS


def make_key(**kw):
    base = dict(model="qwen3-32b", rep=7, suite="banking", attack="system_message",
                task="user_task_0", injection="injection_task_1")
    return Key(**{**base, **kw})


def write(folder, key, verdict):
    target = Path(folder) / episode_path(key, verdict)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}")
    return target


class Grid(unittest.TestCase):
    def test_198_episodes_per_repetition(self):
        self.assertEqual(EPISODES_PER_REP, 198)

    def test_grid_is_not_uniform(self):
        self.assertEqual(GRID["workspace"], (5, 3))
        self.assertEqual(GRID["slack"], (3, 2))
        self.assertEqual(sum(t * i for t, i in GRID.values()) * len(ATTACKS), 198)

    def test_each_model_has_hours_and_a_price(self):
        for m in CAMPAIGN_MODELS:
            self.assertIn(m, CHUNK_HOURS)
            self.assertIn(m, MODELS)  # without a price, the cap cannot see it
            self.assertNotIn("__", m)  # separator in file names
            self.assertNotIn("/", m)  # becomes a folder name

    def test_chunks_for_one_model(self):
        c = chunks(["gpt-oss-120b"])
        self.assertEqual({m for m, _ in c}, {"gpt-oss-120b"})
        self.assertEqual(len(c), TARGET_REPS)

    def test_all_chunks(self):
        c = chunks()
        self.assertEqual(len(c), len(CAMPAIGN_MODELS) * TARGET_REPS)
        self.assertEqual(len(set(c)), len(c))
        self.assertNotIn(0, [rep for _, rep in c])


class FileName(unittest.TestCase):
    def test_repetition_in_the_name(self):
        n = file_name(make_key(), WON)
        self.assertTrue(n.startswith("VENCEU__openclaw__qwen3-32b__banking__"))
        self.assertTrue(n.endswith("__system_message__rep7.json"))

    def test_standalone_run_has_no_suffix(self):
        n = file_name(make_key(rep=0), BLOCKED)
        self.assertTrue(n.endswith("__system_message.json"))

    def test_parse_is_the_inverse_of_file_name(self):
        for rep in (0, 1, 10):
            key = make_key(rep=rep)
            for verdict in (WON, BLOCKED, INVALID):
                self.assertEqual(parse_name(file_name(key, verdict)), key)

    def test_parse_ignores_other_names(self):
        for name in ("resumo-da-execucao.json", "notes.txt",
                     "VENCEU__attack_doc__rep1__abc.json"):
            self.assertIsNone(parse_name(name))

    def test_parse_refuses_a_path(self):
        n = file_name(make_key(), WON)
        self.assertIsNone(parse_name(f"qwen3-32b/rep07/{n}"))
        self.assertIsNotNone(parse_name(n))

    def test_pattern_matches_any_verdict(self):
        key = make_key()
        with tempfile.TemporaryDirectory() as d:
            write(d, key, WON)
            self.assertEqual(len(list(Path(d).glob("**/" + name_pattern(key)))), 1)


class ReplaceInsteadOfDuplicate(unittest.TestCase):
    """One file per (cell, repetition), even if the verdict changes."""

    @staticmethod
    def count(folder):
        return len(list(Path(folder).rglob("*.json")))

    def test_removes_the_previous_verdict(self):
        key = make_key(rep=3, suite="travel", attack="direct")
        with tempfile.TemporaryDirectory() as d:
            write(d, key, BLOCKED)
            self.assertEqual(remove_previous(key, d), 1)
            write(d, key, WON)
            self.assertEqual(self.count(d), 1)
            self.assertEqual(done_keys(d), {key})

    def test_keeps_other_repetitions(self):
        with tempfile.TemporaryDirectory() as d:
            for rep in (0, 1, 2):
                write(d, make_key(rep=rep), BLOCKED)
            self.assertEqual(remove_previous(make_key(rep=1), d), 1)
            self.assertEqual({k.rep for k in done_keys(d)}, {0, 2})

    def test_also_removes_a_copy_in_a_flat_layout(self):
        key = make_key(rep=4)
        with tempfile.TemporaryDirectory() as d:
            Path(d, file_name(key, BLOCKED)).write_text("{}")
            self.assertEqual(remove_previous(key, d), 1)
            self.assertEqual(self.count(d), 0)

    def test_keeps_other_models(self):
        a, b = make_key(), make_key(model="deepseek-flash")
        with tempfile.TemporaryDirectory() as d:
            write(d, a, WON)
            write(d, b, WON)
            self.assertEqual(remove_previous(a, d), 1)
            self.assertEqual({k.model for k in done_keys(d)}, {"deepseek-flash"})


class Layout(unittest.TestCase):
    def test_subfolder_is_model_and_repetition(self):
        self.assertEqual(subfolder(make_key()), Path("qwen3-32b") / "rep07")

    def test_leading_zero_sorts_correctly(self):
        names = sorted(subfolder(make_key(rep=r)).name for r in (1, 2, 10))
        self.assertEqual(names, ["rep01", "rep02", "rep10"])

    def test_standalone_run_goes_to_rep00(self):
        self.assertEqual(subfolder(make_key(rep=0)).name, "rep00")

    def test_path_ends_with_the_file_name(self):
        for verdict in (WON, BLOCKED, INVALID):
            self.assertEqual(episode_path(make_key(), verdict).name,
                             file_name(make_key(), verdict))

    def test_finds_nested_and_flat_files(self):
        key = make_key()
        other = make_key(rep=0)
        with tempfile.TemporaryDirectory() as d:
            nested = write(d, key, WON)
            Path(d, file_name(other, BLOCKED)).write_text("{}")
            self.assertEqual({p.name for p in episode_paths(d)},
                             {nested.name, file_name(other, BLOCKED)})
            self.assertEqual(done_keys(d), {key, other})

    def test_missing_folder(self):
        self.assertEqual(episode_paths("/tmp/does-not-exist-98765"), [])
        self.assertEqual(done_keys("/tmp/does-not-exist-12345"), set())


class WorkerSplit(unittest.TestCase):
    PAIR = ["qwen3-32b", "deepseek-flash"]

    def test_parts_cover_everything_without_overlap(self):
        items = chunks()
        a, b = split_chunks(items, 2)
        self.assertEqual(len(a) + len(b), len(items))
        self.assertEqual(set(a) | set(b), set(items))
        self.assertEqual(set(a) & set(b), set())

    def test_each_worker_gets_both_models(self):
        for part in split_chunks(chunks(self.PAIR), 2):
            self.assertEqual({m for m, _ in part}, set(self.PAIR))

    def test_balances_time_not_count(self):
        parts = split_chunks(chunks(self.PAIR), 2)
        hours = [sum(CHUNK_HOURS[m] for m, _ in p) for p in parts]
        self.assertAlmostEqual(hours[0], hours[1], places=6)

    def test_other_worker_counts(self):
        for n in (1, 3, 4):
            parts = split_chunks(chunks(), n)
            self.assertEqual(sum(len(p) for p in parts), len(chunks()))
            hours = [sum(CHUNK_HOURS[m] for m, _ in p) for p in parts]
            self.assertLess(max(hours) - min(hours), 4.5)


if __name__ == "__main__":
    unittest.main()
