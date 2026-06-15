from manufacture_jis.jis_cp_model import JISCPModel
from manufacture_jis.jis_data import JISData
from manufacture_jis import DATA_ROOT


def test_model():
    data = JISData(DATA_ROOT / "sample_input.xlsx")
    model = JISCPModel(data)
    result = model.run()
    vehicles = result.vehicles
    assert len(vehicles) == 2
    assert not result.issues
    result.write_excel()
    