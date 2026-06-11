from pathlib import Path

from manufacture_jis.jis_data import JISData


def test_jis_data_can_load_sample_input() -> None:
    sample_path = Path(__file__).resolve().parents[1] / "data" / "sample_input.xlsx"

    data = JISData(sample_path)

    # item_set
    assert data.item_set == {"item1"}

    # origin = midnight of earliest start time - 1 day
    # start_hour=24, finish_hour=36, processing_time=10, h ranges 24..33
    # arrival_ub = h+1-3 = h-2, max at h=33 → 31
    assert data.t_max == 31

    # packaging
    assert data.pc_per_pallet == {"item1": 10}

    # vehicles: 5 per supplier
    assert len(data.v2vehicle) == 15  # 3 suppliers × 5 vehicles
    assert data.v2vehicle["supplier1_车次1"].capacity == 18
    assert data.v2vehicle["supplier1_车次1"].supplier == "supplier1"
    assert data.v2vehicle["supplier2_车次1"].capacity == 22
    assert data.v2vehicle["supplier3_车次1"].capacity == 18

    # supplier → vehicle sets
    assert len(data.s_to_v_set["supplier1"]) == 5
    assert len(data.s_to_v_set["supplier2"]) == 5
    assert len(data.s_to_v_set["supplier3"]) == 5

    # c_to_v_set: each hourly consumption maps to 5 vehicles of its supplier
    assert len(data.c_to_v_set["demand1_h24"]) == 5
    assert all("supplier1" in v for v in data.c_to_v_set["demand1_h24"])
    assert len(data.c_to_v_set["demand2_h24"]) == 5
    assert all("supplier2" in v for v in data.c_to_v_set["demand2_h24"])

    # s_to_i_set / i_to_s_set / i_to_v_set
    assert data.s_to_i_set["supplier1"] == {"item1"}
    assert data.s_to_i_set["supplier2"] == {"item1"}
    assert data.i_to_s_set["item1"] == {"supplier1", "supplier2"}
    assert data.i_to_v_set["item1"] == data.s_to_v_set["supplier1"] | data.s_to_v_set["supplier2"]

    # si2qty
    assert data.si2qty[("supplier1", "item1")] == 50
    assert data.si2qty[("supplier2", "item1")] == 50

    # hourly splitting: 10 hours per demand, 30 consumptions total
    assert len(data.c2consumption) == 30

    # spot-check demand1_h24 (first hour, h=24)
    c = data.c2consumption["demand1_h24"]
    assert c.mfg_order == "mfg_order1"
    assert c.item == "item1"
    assert c.qty == 5  # 50 // 10
    assert c.consumption_time == 24
    assert c.arrival_lb == 16  # 24 - 8
    assert c.arrival_ub == 22  # (24+1) - 3

    # spot-check demand1_h33 (last hour, h=33)
    c9 = data.c2consumption["demand1_h33"]
    assert c9.qty == 5
    assert c9.consumption_time == 33
    assert c9.arrival_lb == 25  # 33 - 8
    assert c9.arrival_ub == 31  # (33+1) - 3
