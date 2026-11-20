"""Pruebas de la lógica web sin red ni descargas del portal municipal."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

# `uv run pytest` puede añadir `tests/` a sys.path sin añadir la raíz del
# proyecto. La aplicación web es código local, no un paquete instalado.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from webapp import server as webapp_server


@pytest.fixture
def handler(monkeypatch):
    responses = []
    monkeypatch.setattr(
        webapp_server.DemoHandler,
        "_send_json",
        lambda self, payload, status=200: responses.append((status, payload)),
    )
    return object.__new__(webapp_server.DemoHandler), responses


def test_coverage_metadata_is_parsed_and_cached(monkeypatch):
    html = """
    <html><body>
      <div>Cobertura temporal</div>
      <div>Desde 1 de septiembre de 2014</div>
      <div>Hasta 30 de junio de 2026</div>
      <div>Fecha de actualización</div>
      <div>20 de octubre de 2026</div>
    </body></html>
    """

    class FakeResponse:
        text = html

        @staticmethod
        def raise_for_status():
            return None

    calls = []
    monkeypatch.setattr(webapp_server, "_coverage_cache", {"expires_at": 0.0, "payload": None})
    monkeypatch.setattr(webapp_server.requests, "get", lambda *args, **kwargs: calls.append(args[0]) or FakeResponse())

    first_payload = webapp_server._load_dataset_coverage()
    second_payload = webapp_server._load_dataset_coverage()

    assert first_payload["available_through"] == {"year": 2026, "month": 6, "label": "junio 2026"}
    assert first_payload["updated_at"] == "20 de octubre de 2026"
    assert second_payload == first_payload
    assert calls == [webapp_server.DATASET_INFO_URL]


def test_future_year_is_rejected_before_month_load(handler, monkeypatch):
    demo_handler, responses = handler
    monkeypatch.setattr(webapp_server, "_load_month", lambda *_: pytest.fail("No debía intentar descargar datos"))
    future_year = date.today().year + 1

    period = demo_handler._parse_period({"year": [str(future_year)], "month": ["1"]})

    assert period is None
    assert responses[0][0] == 400
    assert "año futuro" in responses[0][1]["error"]


def test_portal_timeout_gets_a_friendly_message():
    error = webapp_server.MadridError("Error al cargar datos")
    error.__cause__ = webapp_server.requests.ReadTimeout("Read timed out")

    message = webapp_server._friendly_portal_error(error)

    assert message is not None
    assert "portal de datos del Ayuntamiento" in message
    assert "inténtalo de nuevo" in message


def test_missing_portal_file_gets_a_friendly_message():
    error = webapp_server.MadridError("No se encontró el fichero CSV para junio de 2026.")

    message = webapp_server._friendly_portal_error(error)

    assert message is not None
    assert "mantenimiento" in message


def test_january_2015_is_accepted_and_summary_is_returned(handler, monkeypatch):
    demo_handler, responses = handler
    period_index = pd.MultiIndex.from_tuples([(1, 2015)])

    class FakeFines:
        data = pd.DataFrame({"fecha": pd.to_datetime(["2015-01-01 08:00:00"])})

        @staticmethod
        def fines_calification():
            return pd.DataFrame([[1]], index=period_index, columns=["LEVE"])

        @staticmethod
        def total_payment():
            return pd.DataFrame([[90.0, 45.0]], index=period_index, columns=["IMP_MAX", "IMP_MIN"])

    monkeypatch.setattr(webapp_server, "_load_month", lambda year, month: FakeFines())

    demo_handler._month_summary({"year": ["2015"], "month": ["1"]})

    assert responses == [(200, {
        "year": 2015,
        "month": 1,
        "records": 1,
        "categories": {"LEVE": 1},
        "payments": {"maximum": 90.0, "minimum": 45.0},
        "hourly": [0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    })]
