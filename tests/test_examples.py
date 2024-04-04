from pathlib import Path
import subprocess

TEST_REPO_PATH = Path(__file__).parent
EXAMPLE_PATH = TEST_REPO_PATH.parent / "examples"


def compare_files(p1, p2):
    # filecmp.cmp does not work because Windows and Linux files have different line breaks
    # (difference in file size, but diff shows no difference)
    # => replace different line break with universal linebreak (by reading with newline=None)
    try:
        with p1.open('r', newline=None) as f1:
            content1 = f1.read()
        with p2.open('r', newline=None) as f2:
            content2 = f2.read()
        return content1 == content2
    except Exception:
        return False


class TestExampleConfigs:
    def test_calculate_costs(self, tmp_path):
        # copy config and results file to tmp, adjust paths
        results_orig = EXAMPLE_PATH / "output/simulation.json"
        results_tmp = tmp_path / "simulation.json"
        results_tmp.write_text(results_orig.read_text())
        src = EXAMPLE_PATH / "configs/calculate_costs.cfg"
        src_text = src.read_text()
        src_text = src_text.replace(
            "examples/simulation.csv", str(EXAMPLE_PATH / "output/simulation.csv"))
        src_text = src_text.replace("examples/simulation.json", str(results_tmp))
        src_text = src_text.replace("examples/data", str(EXAMPLE_PATH / "data"))
        dst = tmp_path / "calculate_costs.cfg"
        dst.write_text(src_text)

        # call calculate cost from shell
        assert subprocess.call([
            "python", TEST_REPO_PATH.parent / "calculate_costs.py", "--config", dst
        ]) == 0
        # check against expected file
        assert compare_files(results_tmp, EXAMPLE_PATH / "output/calculate_costs_results.json")

    def test_generate(self, tmp_path):
        result_path = tmp_path / "scenario.json"
        for config in [
                "generate.cfg", "generate_from_statistics.cfg",
                "generate_from_csv.cfg", "generate_from_simbev.cfg"]:
            # copy config to tmp, adjust paths
            src = EXAMPLE_PATH / f"configs/{config}"
            src_text = src.read_text()
            # write output to tmp
            src_text = src_text.replace("examples/scenario.json", str(result_path))
            # fix path to examples folder
            src_text = src_text.replace("examples/data", str(EXAMPLE_PATH / "data"))
            dst = tmp_path / config
            dst.write_text(src_text)

            # call generate.py from shell
            assert subprocess.call([
                "python", TEST_REPO_PATH.parent / "generate.py", "--config", dst
            ]) == 0
            # check against expected file
            expected = EXAMPLE_PATH / f"output/scenario_{dst.stem}.json"
            if expected.exists():
                assert compare_files(result_path, expected)
            else:
                # can't test from_csv against known file, as results differ each run
                assert result_path.exists()

    def test_generate_schedule(self, tmp_path):
        # copy config and scenario to tmp, adjust paths
        scenario = tmp_path / "scenario.json"
        scenario.write_text((EXAMPLE_PATH / "scenario.json").read_text())
        src = EXAMPLE_PATH / "configs/generate_schedule.cfg"
        src_text = src.read_text()
        # adjust examples path
        src_text = src_text.replace("examples/data", str(EXAMPLE_PATH / "data"))
        # use tmp scenario
        src_text = src_text.replace("examples/scenario.json", str(scenario))
        # write output to tmp
        schedule = tmp_path / "schedule.csv"
        src_text = src_text.replace("examples/schedule_example.csv", str(schedule))
        # no visual output
        src_text = src_text.replace("visual = true", "visual = false")
        dst = tmp_path / "generate_schedule.cfg"
        dst.write_text(src_text)
        # call generate_schedule.py from shell
        assert subprocess.call([
            "python", TEST_REPO_PATH.parent / "generate_schedule.py", "--config", dst
        ]) == 0
        # check against expected file
        assert compare_files(schedule, EXAMPLE_PATH / "output/schedule.csv")

    def test_simulate(self, tmp_path):
        # copy config to tmp, adjust paths
        src = EXAMPLE_PATH / "configs/simulate.cfg"
        src_text = src.read_text()
        # adjust input file path
        src_text = src_text.replace("examples/scenario.json", str(EXAMPLE_PATH / "scenario.json"))
        src_text = src_text.replace("examples/data", str(EXAMPLE_PATH / "data"))
        # adjust output paths
        src_text = src_text.replace("examples/simulation", str(tmp_path / "simulation"))
        # no visual output
        src_text = src_text.replace("visual = true", "visual = false")
        dst = tmp_path / "simulate.cfg"
        dst.write_text(src_text)
        # call simulate.py from shell
        assert subprocess.call([
            "python", TEST_REPO_PATH.parent / "simulate.py", "--config", dst
        ]) == 0
        # check against expected files
        for p in tmp_path.glob("simulation*"):
            assert compare_files(p, EXAMPLE_PATH / f"output/{p.name}"), f"{p.name} differs"
