from agent_service.agents.layers import match_layer, normalize_name

CAPAS = [{"name": "Parques_Lima"}, {"name": "parques"}, {"name": "Rios-Peru"}]


def test_normalize_name():
    assert normalize_name("  Parques_Lima-Norte ") == "parques lima norte"


def test_prefiere_coincidencia_exacta():
    assert match_layer("PARQUES", CAPAS) == {"name": "parques"}


def test_hint_contenido_en_el_nombre():
    assert match_layer("rios", CAPAS) == {"name": "Rios-Peru"}


def test_nombre_contenido_en_el_hint_solo_si_se_pide():
    assert match_layer("capa rios peru 2024", CAPAS) is None
    assert match_layer("capa rios peru 2024", CAPAS, hint_may_contain_name=True) == {"name": "Rios-Peru"}


def test_sin_hint_o_sin_coincidencia():
    assert match_layer("", CAPAS) is None
    assert match_layer("vias", CAPAS) is None
    assert match_layer("x", [{"name": ""}], hint_may_contain_name=True) is None
