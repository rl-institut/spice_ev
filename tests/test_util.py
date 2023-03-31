import argparse
import datetime
import pytest

from spice_ev import components, util


class TestUtil:

    def test_datetime_from_isoformat(self):
        # no data
        assert util.datetime_from_isoformat(None) is None
        # naive datetime
        dt = datetime.datetime(2023, 1, 1, 13, 37, 0)
        dt_str = dt.isoformat()
        assert dt == util.datetime_from_isoformat(dt_str)
        # timezone-aware
        dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=1)))
        dt_str = dt.isoformat()
        assert dt == util.datetime_from_isoformat(dt_str)

    def test_time_window(self):

        time_windows = {
            "autumn": {
                # 01.09. - 30.11., 16:30 - 20:00
                "start": datetime.datetime(day=1, month=9, year=1900, hour=16, minute=30),
                "end": datetime.datetime(day=30, month=11, year=1900, hour=20, minute=0),
            },
            # split winter (year changes)
            "winter1": {
                # 01.12. - 31.12., 16:30 - 19:30
                "start": datetime.datetime(day=1, month=12, year=1900, hour=16, minute=30),
                "end": datetime.datetime(day=31, month=12, year=1900, hour=19, minute=30),
            },
            "winter2": {
                # 01.01. - 28.02., 16:30 - 19:30
                "start": datetime.datetime(day=1, month=1, year=1900, hour=16, minute=30),
                "end": datetime.datetime(day=28, month=2, year=1900, hour=19, minute=30),
            }
        }

        not_in_window = [
            datetime.datetime(2021, 8, 31, 23, 59),
            datetime.datetime(2022, 9, 1, 0, 00),
            datetime.datetime(2023, 12, 31, 20, 0),
            datetime.datetime(2024, 9, 1, 20, 0),
        ]
        in_window = [
            datetime.datetime(2021, 9, 1, 16, 30),
            datetime.datetime(2022, 9, 3, 19, 59),
            datetime.datetime(2023, 1, 1, 18, 0),
        ]

        for dt in not_in_window:
            assert not util.datetime_within_window(dt, time_windows)
        for dt in in_window:
            assert util.datetime_within_window(dt, time_windows)

    def test_core_window(self):
        dt = datetime.datetime(day=1, month=1, year=2020)
        assert util.dt_within_core_standing_time(dt, None)

        # 2020/1/1 is Wednesday (3)
        assert util.dt_within_core_standing_time(dt, {"no_drive_days": [2]})
        assert util.dt_within_core_standing_time(dt, {"no_drive_days": [0, 2]})
        assert util.dt_within_core_standing_time(dt, {"no_drive_days": [2, 3]})
        assert not util.dt_within_core_standing_time(dt, {"no_drive_days": [3]})
        assert not util.dt_within_core_standing_time(dt, {"no_drive_days": []})

        assert not util.dt_within_core_standing_time(dt, {"holidays": []})
        assert not util.dt_within_core_standing_time(dt, {"holidays": ["2021-01-01"]})
        assert util.dt_within_core_standing_time(dt, {"holidays": ["2020-01-01"]})

        assert not util.dt_within_core_standing_time(dt, {})
        core = {"times": [{"start": (10, 0), "end": (12, 30)}]}
        for h, m, e in [(0, 0, False), (12, 0, True), (12, 45, False)]:
            b = util.dt_within_core_standing_time(dt.replace(hour=h, minute=m), core)
            assert b == e, "{}:{} is {}".format(h, m, b)

        core = {"times": [{"start": (22, 0), "end": (5, 30)}]}
        for h, m, e in [(18, 0, False), (23, 0, True), (0, 0, True), (5, 0, True), (5, 45, False)]:
            b = util.dt_within_core_standing_time(dt.replace(hour=h, minute=m), core)
            assert b == e, "{}:{} is {}".format(h, m, b)

        core = {"times": [
            {"start": (22, 30), "end": (5, 30)},
            {"start": (10, 0), "end": (13, 0)}
        ]}
        # bool hours 2         1         0
        #         321098765432109876543210
        e_vec = 0b100000000011110000111111
        for h in range(24):
            e = ((e_vec >> h) & 1) == 1
            b = util.dt_within_core_standing_time(dt.replace(hour=h, minute=0), core)
            assert b == e, "{}:{} is {}".format(h, 0, b)

    def test_get_cost_and_power(self):
        assert util.get_power(None, {}) is None
        # fixed costs
        costs = {"type": "fixed", "value": 3}
        power_cost = [(0, 0), (1, 3), (-1, -3)]
        for p, c in power_cost:
            assert pytest.approx(util.get_cost(p, costs)) == c
            assert pytest.approx(util.get_power(c, costs)) == p

        # poly costs
        # order 0 or 1 (constant cost). Also test order reducing (highest order != 0)
        costs = {"type": "polynomial", "value": []}
        assert util.get_power(0, costs) is None
        costs = {"type": "polynomial", "value": [1, 0, 0, 0]}
        assert util.get_power(1, costs) is None
        # linear: c = 2 - x
        costs = {"type": "polynomial", "value": [2, -1]}
        power_cost = [(0, 2), (1, 1), (-1, 3)]
        for p, c in power_cost:
            assert pytest.approx(util.get_cost(p, costs)) == c
            assert pytest.approx(util.get_power(c, costs)) == p

        # c = 3 + 2*x + 1*x*x
        costs = {"type": "polynomial", "value": [3, 2, 1]}
        power_cost = [(0, 3), (1, 6), (-1, 2)]  # 3+0+0, 3+2+1, 3-2+1
        for p, c in power_cost:
            assert pytest.approx(util.get_cost(p, costs)) == c
            assert pytest.approx(util.get_power(c, costs)) == p

        # higher order: not supported
        costs = {"type": "polynomial", "value": [3, 2, 1, 0, -1]}
        with pytest.raises(NotImplementedError):
            util.get_power(0, costs)

        # unknown type
        costs = {"type": None}
        with pytest.raises(NotImplementedError):
            util.get_cost(0, costs)
        with pytest.raises(NotImplementedError):
            util.get_power(0, costs)

    def test_clamp_power(self):
        cs = components.ChargingStation({
            "min_power": 1,
            "max_power": 10,
            "current_power": 0,
            "parent": "GC"
        })
        vtype = components.VehicleType({
            "name": "test",
            "capacity": 1,
            "charging_curve": [(0, 1), (1, 0)],
            "min_charging_power": 0
        })
        v = components.Vehicle({
            "vehicle_type": "test",
        }, {"test": vtype})

        assert util.clamp_power(0, v, cs) == 0
        assert util.clamp_power(-1, v, cs) == 0
        assert util.clamp_power(0.5, v, cs) == 0
        assert util.clamp_power(1, v, cs) == 1
        assert util.clamp_power(2, v, cs) == 2
        assert util.clamp_power(11, v, cs) == 10

        vtype.min_charging_power = 2

        assert util.clamp_power(1, v, cs) == 0
        assert util.clamp_power(1.5, v, cs) == 0
        assert util.clamp_power(2, v, cs) == 2
        assert util.clamp_power(3, v, cs) == 3
        assert util.clamp_power(11, v, cs) == 10

        cs.current_power = 1

        assert util.clamp_power(0, v, cs) == 0
        # still below vehicle min_power
        assert util.clamp_power(0.9, v, cs) == 0
        # not below vehicle min_power as already charging 1 kWh
        assert util.clamp_power(1.5, v, cs) == 1.5
        assert util.clamp_power(3, v, cs) == 3
        assert util.clamp_power(9, v, cs) == 9
        assert util.clamp_power(10, v, cs) == 9
        assert util.clamp_power(20, v, cs) == 9

    def test_set_options_from_config(self, tmp_path):
        ns = argparse.Namespace(baf=2)
        # create dummy config:
        """
        foo= bar

        # comment
        baf =1
        array = [1]
        """
        (tmp_path / "config.cfg").write_text("foo= bar\n\n#comment\nbaf =1\narray = [1]")
        # no config: no update
        util.set_options_from_config(ns)
        assert ns.baf == 2

        # config without parser: simple update, no check
        ns.config = tmp_path / "config.cfg"
        util.set_options_from_config(ns)
        assert ns.baf == 1
        assert ns.foo == "bar"
        assert ns.array == [1]

        # config with parser: check validity of options
        # reset Namespace
        ns = argparse.Namespace(baf=2, config=tmp_path / "config.cfg")
        parser = argparse.ArgumentParser()
        # unknown option: generic exception
        with pytest.raises(Exception):
            util.set_options_from_config(ns, check=parser)
        # add options, but wrong type of foo
        parser.add_argument("--foo", type=int)
        parser.add_argument("--baf", type=int)
        parser.add_argument("--array", action="append")
        # unused option
        parser.add_argument("--default", default="default")
        with pytest.raises(ValueError):
            util.set_options_from_config(ns, check=parser)
        # fix last option
        parser._actions[-4].type = str
        util.set_options_from_config(ns, check=parser)
        assert ns.baf == 1
        assert ns.foo == "bar"
        assert ns.array == [1]
        # check choices
        parser._actions[-2].choices = [2, 3]
        with pytest.raises(argparse.ArgumentError):
            util.set_options_from_config(ns, check=parser)
        parser._actions[-2].choices = [1, 2, 3]
        util.set_options_from_config(ns, check=parser)

    def test_sanitize(self):
        # default: remove </|\\>:"?*
        assert util.sanitize("") == ""
        assert util.sanitize("foo bar") == "foo bar"
        assert util.sanitize('".*<f/|o\\o:>?!"') == ".foo!"
        # declare special chars to remove
        assert util.sanitize("<foo? bar!>", 'or ') == "<f?ba!>"
