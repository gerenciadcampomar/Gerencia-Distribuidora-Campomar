from campomar_compras.code_mapping import CodeMappingStore


def test_mapeo_confirmado_nossovitch(tmp_path):
    store = CodeMappingStore("nossovitch", providers_dir=tmp_path)
    store.learn("RNMI 37A859", "RERNMI37A859")
    store.learn("ZEN 5562", "Z5562")

    assert store.get_centum_code("RNMI 37A859") == "RERNMI37A859"
    assert store.get_centum_code("ZEN 5562") == "Z5562"
    # normaliza espacios/mayúsculas al buscar
    assert store.get_centum_code("rnmi37a859") == "RERNMI37A859"


def test_mapeo_persiste_en_disco(tmp_path):
    store = CodeMappingStore("nossovitch", providers_dir=tmp_path)
    store.learn("RNMI 37A859", "RERNMI37A859")

    store2 = CodeMappingStore("nossovitch", providers_dir=tmp_path)
    assert store2.get_centum_code("RNMI 37A859") == "RERNMI37A859"


def test_suggest_sin_mapeo_previo(tmp_path):
    store = CodeMappingStore("nossovitch", providers_dir=tmp_path)
    sugerencias = store.suggest("RNMI 37A859", ["RERNMI37A859", "Z5562", "Z837"])
    assert sugerencias
    assert sugerencias[0][0] == "RERNMI37A859"
