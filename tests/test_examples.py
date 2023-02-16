import json
from pathlib import Path
import subprocess

from spice_ev.scenario import Scenario

TEST_REPO_PATH = Path(__file__).parent
EXAMPLE_PATH = TEST_REPO_PATH.parent / "examples"


class TestExampleConfigs:
    def test_calculate_costs(self, tmp_path):
        # setup scenario
        with (EXAMPLE_PATH / "scenario.json").open('r') as f:
            j = json.load(f)
        s = Scenario(j)
        save_results = tmp_path / "save_results.json"
        save_timeseries = tmp_path / "save_timeseries.csv"

        # run scenario
        s.run("greedy", {
            "save_results": str(save_results),
            "save_timeseries": str(save_timeseries)
        })

        # copy config to tmp, adjust paths
        src = EXAMPLE_PATH / "configs/calculate_costs.cfg"
        src_text = src.read_text()
        src_text = src_text.replace("examples/simulation.csv", str(save_timeseries))
        src_text = src_text.replace("examples/simulation.json", str(save_results))
        src_text = src_text.replace("examples/data", str(EXAMPLE_PATH / "data"))
        dst = tmp_path / "calculate_costs.cfg"
        dst.write_text(src_text)

        # call calculate cost from shell
        assert subprocess.call([
            "python", TEST_REPO_PATH.parent / "calculate_costs.py", "--config", dst
        ]) == 0
        with save_results.open() as f:
            results = json.load(f)
        assert "costs" in results
        assert results["costs"]["electricity costs"]["per year"]["total (gross)"] == 203.54

    def test_generate(self, tmp_path):
        for config in [
                "generate.cfg", "generate_from_statistics.cfg",
                "generate_from_csv.cfg", "generate_from_simbev.cfg"]:
            # copy config to tmp, adjust paths
            src = EXAMPLE_PATH / f"configs/{config}"
            src_text = src.read_text()
            # write output to tmp
            src_text = src_text.replace("examples/scenario.json", str(tmp_path / "scenario.json"))
            # fix path to examples folder
            src_text = src_text.replace("examples", str(EXAMPLE_PATH))
            dst = tmp_path / config
            dst.write_text(src_text)

            # call generate.py from shell
            assert subprocess.call([
                "python", TEST_REPO_PATH.parent / "generate.py", "--config", dst
            ]) == 0
            assert (tmp_path / "scenario.json").is_file()

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
        assert schedule.is_file()

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
