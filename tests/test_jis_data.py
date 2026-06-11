from manufacture_jis import DATA_ROOT
from manufacture_jis.jis_data import JISData


def test_jis_data_can_load_sample_input() -> None:
    sample_path = DATA_ROOT / "sample_input.xlsx"

    data = JISData(sample_path)

    # item_set
    assert data.item_set == {"item1"}

    assert data.t_max == 25

    # packaging
    assert data.pc_per_pallet == {"item1": 10}

    # s_to_i_set / i_to_s_set / i_to_v_set
    assert data.s_to_i_set["supplier1"] == {"item1"}
    assert data.s_to_i_set["supplier2"] == {"item1"}
    assert data.i_to_s_set["item1"] == {"supplier1", "supplier2"}
    assert data.i_to_v_set["item1"] == data.s_to_v_set["supplier1"] | data.s_to_v_set["supplier2"]

    # si2qty
    assert data.si2qty[("supplier1", "item1")] == 50
    assert data.si2qty[("supplier2", "item1")] == 50

    assert len(data.c2consumption) == 5

    # spot-check first hour
    c = data.c2consumption["mfg_order1_h24"]
    assert c.mfg_order == "mfg_order1"
    assert c.item == "item1"
    assert c.qty == 20
    assert c.consumption_time == 24
    assert c.arrival_lb == 16  # 24 - 8
    assert c.arrival_ub == 21  # 24 - 3
