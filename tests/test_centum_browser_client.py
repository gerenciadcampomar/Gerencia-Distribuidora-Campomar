from campomar_compras.centum_browser_client import _candidatas_por_cantidad


class _FakeField:
    def __init__(self, value=None, checked=None):
        self._value = value
        self._checked = checked

    def input_value(self):
        return self._value

    def is_checked(self):
        return self._checked


class _FakeFieldGroup:
    def __init__(self, fields):
        self._fields = fields

    def count(self):
        return len(self._fields)

    @property
    def first(self):
        return self._fields[0]


class _FakeFila:
    """Simula el <tr> real lo mínimo necesario para _candidatas_por_cantidad:
    un campo de cantidad editable y un checkbox, cada uno con su valor
    actual en pantalla."""

    def __init__(self, cantidad, checked=False):
        self.cantidad = cantidad
        self.checked = checked

    def locator(self, selector):
        if "CantidadUnidadNivel" in selector:
            return _FakeFieldGroup([_FakeField(value=str(self.cantidad))])
        if "checkbox" in selector:
            return _FakeFieldGroup([_FakeField(checked=self.checked)])
        raise AssertionError(f"selector inesperado en el fake: {selector!r}")


class _FakeFilas:
    def __init__(self, filas):
        self._filas = filas

    def all(self):
        return self._filas

    @property
    def first(self):
        return self._filas[0]


def test_candidatas_por_cantidad_sin_cantidad_esperada_devuelve_vacio():
    # Caso real (MD15772/1): sin cantidad_esperada, los llamadores caen de
    # nuevo en .first -- la lista vacía es la señal para eso.
    filas = _FakeFilas([_FakeFila(10), _FakeFila(30)])
    assert _candidatas_por_cantidad(filas, None) == []


def test_candidatas_por_cantidad_filtra_solo_las_que_coinciden():
    # Caso real: grupo MD15772/1 con filas de 10, 10, 30, 30, 30 -- pedir
    # las de cantidad 30 no debe devolver ninguna de las de 10.
    filas = _FakeFilas([_FakeFila(10), _FakeFila(10), _FakeFila(30), _FakeFila(30), _FakeFila(30)])
    candidatas = _candidatas_por_cantidad(filas, 30)
    assert len(candidatas) == 3
    assert all(c.cantidad == 30 for c in candidatas)


def test_candidatas_por_cantidad_devuelve_varias_cuando_hay_empate():
    # Dos filas con la MISMA cantidad esperada (10 y 10): las dos deben
    # aparecer como candidatas, para que el llamador pueda descartar la
    # ya usada (tildada) y quedarse con la otra -- si acá se devolviera
    # una sola, dos eliminaciones distintas terminarían apuntando siempre
    # a la misma fila física.
    filas = _FakeFilas([_FakeFila(10, checked=True), _FakeFila(10, checked=False), _FakeFila(30)])
    candidatas = _candidatas_por_cantidad(filas, 10)
    assert len(candidatas) == 2
    assert [c.checked for c in candidatas] == [True, False]


def test_candidatas_por_cantidad_ignora_filas_sin_el_campo_cantidad():
    class _FilaSinCampo:
        def locator(self, selector):
            return _FakeFieldGroup([])

    filas = _FakeFilas([_FilaSinCampo(), _FakeFila(10)])
    candidatas = _candidatas_por_cantidad(filas, 10)
    assert len(candidatas) == 1
