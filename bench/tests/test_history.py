from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from mc5000_bench.charger import find_charger
from mc5000_bench.chart import nice_ticks, value_at
from mc5000_bench.history import Archive, Run, curve_for, find_runs, merge_runs, parse_history

from fakeha import TOKEN, FakeHA, registry, sample_history


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def wait_for(qapp, cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_find_runs():
    now = time.time()
    history = parse_history(sample_history(now))
    charger = find_charger(registry())
    runs = {s: find_runs(s, charger, history, now) for s in (1, 2, 3, 4)}
    (ebl,) = runs[2]  # the HA restart mid-run didn't split it
    assert ebl.outcome == "Connection break"
    assert ebl.capacity == 1160
    assert round(ebl.elapsed_s) == 8355
    assert ebl.chemistry == "nimh" and ebl.operation == "discharge"
    (storage,) = runs[3]
    assert storage.outcome == "Done" and storage.operation == "storage"
    (live,) = runs[1]
    assert live.end is None and live.outcome == "Running"
    assert runs[4] == []
    curve = curve_for(ebl, charger, history)
    assert len(curve["voltage"]) > 100
    assert curve["voltage"][0][1] == pytest.approx(1.49)
    assert curve["temperature"] == []


def test_archive_and_merge(tmp_path):
    archive = Archive(tmp_path / "runs")
    old = Run(2, 1000.0, 2000.0, "nimh", "discharge", "Done", 900, 1000,
              curve={"voltage": [(1000.0, 1.4), (2000.0, 1.0)]})
    archive.save(old)
    assert archive.has(old.run_id)
    loaded = archive.load(old.run_id)
    assert loaded.curve["voltage"][1] == (2000.0, 1.0)
    assert archive.all()[0].curve == {}
    newer = Run(1, 5000.0, None)
    merged = merge_runs([newer, Run(2, 1000.0, 2000.0, outcome="Stopped")], archive.all())
    assert [r.run_id for r in merged] == ["1-5000", "2-1000"]
    assert merged[1].outcome == "Stopped"  # HA wins over the archive


def test_chart_helpers():
    assert nice_ticks(0, 1) == [0, 0.25, 0.5, 0.75, 1.0]
    assert nice_ticks(1.2, 1.5)[0] >= 1.2
    pts = [(0, 1.0), (10, 2.0)]
    assert value_at(pts, 5) == 1.0 and value_at(pts, 10) == 2.0 and value_at(pts, -1) is None


def test_service_and_dialogs(qapp, tmp_path):
    from mc5000_bench import app as appmod
    from mc5000_bench.config import Settings, save_settings
    from mc5000_bench.history_ui import CurveDialog, HistoryDialog

    ha = FakeHA()
    save_settings(Settings(ha.url, TOKEN))
    window = appmod.MainWindow()
    archive = window.history.archive
    # After connecting, the window copies finished runs into the archive.
    assert wait_for(qapp, lambda: archive.has(next(
        (r.run_id for r in archive.all() if r.slot == 3), "x")) and len(archive.all()) == 2)
    saved = {r.slot: archive.load(r.run_id) for r in archive.all()}
    assert saved[2].capacity == 1160 and len(saved[2].curve["voltage"]) > 100

    dialog = HistoryDialog(window.history, window)
    assert wait_for(qapp, lambda: dialog.table.rowCount() == 3)
    assert dialog.table.item(0, 4).text() == "Running"
    assert dialog.table.item(2, 4).text() == "Connection break"
    dialog.table.selectRow(2)
    dialog._select(2)
    assert wait_for(qapp, lambda: dialog.view.chart.data.get("voltage"))
    dialog.filter.setCurrentIndex(3)
    assert dialog.table.rowCount() == 1
    dialog.done(0)

    curve = CurveDialog(1, window.history, window)
    assert wait_for(qapp, lambda: len(curve.view.chart.data.get("voltage", [])) > 50)
    before = len(curve.view.chart.data["voltage"])
    time.sleep(0.05)
    ha.push({"sensor.skyrc_mc5000_4036_slot_1_voltage": "1.420"})
    window._curve_dialogs.append(curve)
    assert wait_for(qapp, lambda: len(curve.view.chart.data["voltage"]) > before)
    assert curve.view.chart.data["voltage"][-1][1] == pytest.approx(1.42)
    curve.done(0)
    window._curve_dialogs.remove(curve)
    window.close()



def test_cycle_run_reports_discharged_capacity():
    charger = find_charger(registry())
    st = "sensor.skyrc_mc5000_4036_slot_4_status"
    cap = "sensor.skyrc_mc5000_4036_slot_4_capacity"
    raw = {st: [{"s": "discharging", "lu": 0}, {"s": "resting", "lu": 1000},
                {"s": "charging", "lu": 1600}, {"s": "done", "lu": 9000}],
           cap: [{"s": "300", "lu": 900}, {"s": "1950", "lu": 999}, {"s": "0", "lu": 1600},
                 {"s": "2010", "lu": 8900}],
           "sensor.skyrc_mc5000_4036_slot_4_operation": [{"s": "cycle", "lu": 0}]}
    (run,) = find_runs(4, charger, parse_history(raw), 10000)
    assert run.outcome == "Done"
    assert run.discharged == 1950 and run.capacity == 2010
    from mc5000_bench.history_ui import capacity_text
    assert capacity_text(run) == "1950 mAh out"


def test_charger_curve_fills_in_voltage(qapp, tmp_path):
    from mc5000_bench import app as appmod
    from mc5000_bench.config import Settings, save_settings
    from mc5000_bench.history_ui import CurveDialog

    ha = FakeHA()
    # Slot 1 has been charging for 2850 s: give it a 1-second curve.
    ha.charger_curves["1"] = {"slot": 1, "available": True, "interval": 1,
                              "voltages": [1.30 + 0.112 * i / 2850 for i in range(2851)]}
    save_settings(Settings(ha.url, TOKEN))
    window = appmod.MainWindow()
    curve = CurveDialog(1, window.history, window)
    assert wait_for(qapp, lambda: len(curve.view.chart.data.get("voltage", [])) > 2000)
    assert "every second" in curve.view.hint.text()
    assert any(c.get("service") == "get_history" for c in ha.calls)
    # Current still comes from HA's history.
    assert 50 < len(curve.view.chart.data["current"]) < 200
    curve.done(0)
    window.close()


def test_charger_curve_fetched_after_finish_ends_at_run_end():
    """A finished run's curve stops growing, so it lines up with the run's end."""
    from types import SimpleNamespace

    from mc5000_bench.history_ui import HistoryService

    # Like the Eneloop test: a 3936 s charge at 16 s per sample, read 17 min after it ended.
    start, end = 10_000.0, 13_936.0
    volts = [1.38 + 0.15 * i / 246 for i in range(247)]
    fake = SimpleNamespace(charger_curves={1: (end + 1020, 16, volts)})
    run = Run(1, start, end, curve={"voltage": [(start + 5, 1.30), (end, 1.52)]})
    HistoryService.apply_charger_curve(fake, run)
    points = run.curve["voltage"]
    assert len(points) == 247
    assert points[0] == (start, 1.38)
    assert points[-1][0] == end
    # The same copy taken mid-run (fetched before the end) still anchors at the fetch.
    run2 = Run(1, start, None, curve={"voltage": []})
    fake.charger_curves[1] = (start + 2000, 8, volts[:251])
    HistoryService.apply_charger_curve(fake, run2)
    assert run2.curve["voltage"][-1][0] == start + 2000


def test_curve_fetched_once_more_after_a_run_ends(qapp, tmp_path):
    from mc5000_bench import app as appmod
    from mc5000_bench.config import Settings, save_settings

    ha = FakeHA()
    save_settings(Settings(ha.url, TOKEN))
    window = appmod.MainWindow()
    assert wait_for(qapp, lambda: window.online)
    active = {s for s, c in window.cards.items() if c.reading.active}
    assert active
    fetched: list[int] = []
    window.history.fetch_charger_curve = fetched.append
    window._fetch_charger_curves()
    assert fetched == sorted(active)
    # Every slot goes idle: each one that was running gets one last fetch, then none.
    for card in window.cards.values():
        card.reading = SimpleReading()
    fetched.clear()
    window._fetch_charger_curves()
    assert fetched == sorted(active)
    fetched.clear()
    window._fetch_charger_curves()
    assert fetched == []
    window.close()


class SimpleReading:
    active = False
