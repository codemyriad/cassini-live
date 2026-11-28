"""Pinned-inventory support contract; no network and no Docker needed."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "check_nc_support", Path(__file__).resolve().parents[1] / "scripts/check_nc_support.py"
)
nc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nc)


def inventory(minimum="33.0.9", maximum_major=35, reference="nc35", majors=(33, 34, 35)):
    return {
        "schema": 1,
        "minimum": minimum,
        "maximum_major": maximum_major,
        "reference": reference,
        "baselines": [
            {"id": f"nc{m}", "nextcloud_version": minimum if m == nc.major(minimum)
             else f"{m}.0.0"}
            for m in majors
        ],
    }


class LockTest(unittest.TestCase):
    def test_committed_lock_parses(self):
        lock = nc.read_lock(nc.LOCK)
        self.assertEqual(lock["repo"].count("/"), 1)
        self.assertEqual(len(lock["sha"]), 40)

    def test_rejects_short_or_symbolic_ref(self):
        for sha in ("main", "0357de6b", ""):
            with self.assertRaises(AssertionError):
                nc.read_lock(self.lock_file(sha))

    def lock_file(self, sha):
        import tempfile

        handle = tempfile.NamedTemporaryFile("w", suffix=".lock", delete=False)
        self.addCleanup(Path(handle.name).unlink)
        handle.write(f"# comment\nrepo=codemyriad/gocassini\nsha={sha}\n")
        handle.close()
        return handle.name


class ValidateTest(unittest.TestCase):
    def test_accepts_contiguous_baselines(self):
        self.assertEqual(nc.validate(inventory()), (33, 35))

    def test_rejects_a_major_hole(self):
        with self.assertRaises(AssertionError):
            nc.validate(inventory(majors=(33, 35)))

    def test_rejects_missing_exact_minimum(self):
        policy = inventory()
        policy["baselines"][0]["nextcloud_version"] = "33.0.8"
        with self.assertRaises(AssertionError):
            nc.validate(policy)

    def test_rejects_unknown_reference(self):
        with self.assertRaises(AssertionError):
            nc.validate(inventory(reference="nc36"))


class CheckTest(unittest.TestCase):
    def test_repo_manifest_matches_the_pinned_window(self):
        # The committed manifest advertises exactly the inventory this fixture
        # mirrors (33..35); check() reads it from the repo root.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            nc.check(inventory())
        self.assertIn("matches the pinned harness inventory", output.getvalue())

    def test_rejects_a_wider_advertised_window(self):
        with self.assertRaises(AssertionError):
            nc.check(inventory(maximum_major=36, reference="nc36", majors=(33, 34, 35, 36)))

    def test_rejects_a_raised_minimum(self):
        with self.assertRaises(AssertionError):
            nc.check(inventory(minimum="34.0.0", reference="nc35", majors=(34, 35)))


class MatrixTest(unittest.TestCase):
    def legs(self, tier):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            nc.matrix(inventory(), tier)
        return [leg["baseline"] for leg in json.loads(output.getvalue())["include"]]

    def test_all_runs_every_baseline(self):
        self.assertEqual(self.legs("all"), ["nc33", "nc34", "nc35"])

    def test_pr_runs_reference_and_exact_minimum(self):
        self.assertEqual(self.legs("pr"), ["nc33", "nc35"])

    def test_pr_deduplicates_when_reference_is_the_minimum(self):
        output = io.StringIO()
        policy = inventory(minimum="35.0.0", reference="nc35", majors=(35,))
        with contextlib.redirect_stdout(output):
            nc.matrix(policy, "pr")
        self.assertEqual(json.loads(output.getvalue()), {"include": [{"baseline": "nc35"}]})


class CliTest(unittest.TestCase):
    def test_check_reads_a_local_inventory(self):
        import tempfile

        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(Path(handle.name).unlink)
        json.dump(inventory(), handle)
        handle.close()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            nc.main(["--inventory", handle.name, "check"])
        self.assertIn("matches the pinned harness inventory", output.getvalue())


if __name__ == "__main__":
    unittest.main()
