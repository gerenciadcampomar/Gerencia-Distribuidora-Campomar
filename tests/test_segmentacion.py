from campomar_compras.models import repartir_segmentacion


def test_reparto_mitad_y_mitad():
    resultado = repartir_segmentacion(10, {"oficial": 1, "alternativa": 1})
    partes = {r.parte: r.cantidad for r in resultado}
    assert partes == {"oficial": 5.0, "alternativa": 5.0}


def test_reparto_impar_absorbe_redondeo_en_ultima_parte():
    resultado = repartir_segmentacion(7, {"oficial": 1, "alternativa": 1})
    total = sum(r.cantidad for r in resultado)
    assert total == 7.0


def test_reparto_por_cantidad_puntual_no_proporcional():
    # ej.: mandar 3 puntuales a "oficial" y el resto a "alternativa"
    resultado = repartir_segmentacion(10, {"oficial": 3, "alternativa": 7})
    partes = {r.parte: r.cantidad for r in resultado}
    assert partes == {"oficial": 3.0, "alternativa": 7.0}
