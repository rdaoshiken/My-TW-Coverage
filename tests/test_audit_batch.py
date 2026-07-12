"""Tests for scripts/audit_batch.py --tickers / --tickers-file mode (#853).

The coverage repo loads scripts by path (scripts/ is not an importable package),
following the existing test_build_themes.py / test_sync_publish_to_git.py
precedent. Loading audit_batch puts scripts/ on sys.path, so `utils` is then
importable and its REPORTS_DIR can be monkeypatched at a synthetic corpus.
Run with: python3 -m pytest tests/ -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_batch.py"
_spec = importlib.util.spec_from_file_location("audit_batch", _SCRIPT)
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)  # type: ignore[union-attr]
import utils  # noqa: E402  scripts/ is on sys.path after loading audit_batch


def _clean_report(ticker: str, name: str) -> str:
    """A synthetic report that passes every audit_ticker rule.

    8+ non-generic wikilinks, all 4 required sections + metadata, segmented
    supply chain (>=3 lines) and customers/suppliers (>=4 lines), no English,
    no placeholders.
    """
    wl = "[[台積電]]、[[聯發科]]、[[鴻海]]、[[台達電]]、[[日月光投控]]、[[廣達]]、[[緯創]]、[[國巨]]"
    return (
        f"# {ticker} {name}\n"
        "**板塊:** 電子\n"
        "**產業:** 電子零組件\n"
        "**市值:** 1000 百萬台幣\n"
        "**企業價值:** 1200 百萬台幣\n"
        "\n## 業務簡介\n"
        f"本公司提供電源與散熱解決方案，往來對象包含 {wl}。\n"
        "\n## 供應鏈位置\n"
        "**上游 (材料):**\n- 採購 [[碳化矽]] 與 [[銅箔]]\n"
        "**中游 (製造):**\n- 自製電源模組\n"
        "**下游 (應用):**\n- 供貨 [[電動車]] 與 [[資料中心]]\n"
        "\n## 主要客戶及供應商\n"
        "### 主要客戶\n- [[台積電]]\n- [[鴻海]]\n"
        "### 主要供應商\n- [[國巨]]\n- [[華新科]]\n"
        "\n## 財務概況\n"
        "### 估值指標\n| P/E |\n|-----|\n| 15 |\n"
    )


@pytest.fixture()
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthetic Pilot_Reports (clean tickers 1111, 2222) + task.md (Batch 101)."""
    reports = tmp_path / "Pilot_Reports" / "Electronic Components"
    reports.mkdir(parents=True)
    (reports / "1111_甲公司.md").write_text(_clean_report("1111", "甲公司"), encoding="utf-8")
    (reports / "2222_乙公司.md").write_text(_clean_report("2222", "乙公司"), encoding="utf-8")
    task_md = tmp_path / "task.md"
    task_md.write_text(
        "# Tasks\n- [x] **Batch 101**: 1111 甲公司, 2222 乙公司\n", encoding="utf-8"
    )
    monkeypatch.setattr(utils, "REPORTS_DIR", str(tmp_path / "Pilot_Reports"))
    monkeypatch.setattr(utils, "TASK_FILE", str(task_md))
    return tmp_path


# --- (0) regression: pre-existing positional batch mode, refactored path ------
def test_positional_batch_mode_regression(corpus: Path, capsys) -> None:
    rc = ab.main(["101", "-v"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "QUALITY AUDIT: Checking 2 tickers in Batch 101" in out
    assert "1111: CLEAN" in out and "2222: CLEAN" in out
    assert "CLEAN (2): ['1111', '2222']" in out
    assert "NEEDS ENRICHMENT (0): []" in out
    assert "MISSING (0): []" in out
    assert "Score: 2/2 (100%) pass quality audit" in out


# --- (1) ticker-list happy path -----------------------------------------------
def test_tickers_happy_path(corpus: Path, capsys) -> None:
    rc = ab.main(["--tickers", "1111", "2222"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CLEAN (2)" in out
    assert "1111" in out and "2222" in out
    assert "2/2" in out  # identical Score line to batch mode


# --- (2) unknown ticker -> lists them + non-zero exit -------------------------
def test_unknown_ticker_errors_nonzero(corpus: Path, capsys) -> None:
    rc = ab.main(["--tickers", "1111", "9999"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "9999" in out  # the offending ticker is named
    assert "1111" not in out  # fail-fast: the known ticker is never audited/printed
    assert "Score" not in out  # no audit report was produced


# --- (3) mutual exclusion with --batch / --all --------------------------------
def test_tickers_mutually_exclusive_with_all(corpus: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        ab.main(["--tickers", "1111", "--all"])
    assert exc.value.code != 0  # argparse usage error


def test_tickers_mutually_exclusive_with_positional_batch(corpus: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        ab.main(["101", "--tickers", "1111"])
    assert exc.value.code != 0


def test_tickers_mutually_exclusive_with_tickers_file(corpus: Path, tmp_path: Path) -> None:
    listing = tmp_path / "x.txt"
    listing.write_text("2222\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ab.main(["--tickers", "1111", "--tickers-file", str(listing)])
    assert exc.value.code != 0


# --- (4) file input (one ticker per line, blanks ignored) ---------------------
def test_tickers_file_input(corpus: Path, tmp_path: Path, capsys) -> None:
    listing = tmp_path / "tickers.txt"
    listing.write_text("1111\n\n2222\n", encoding="utf-8")  # blank line tolerated
    rc = ab.main(["--tickers-file", str(listing)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CLEAN (2)" in out
    assert "2/2" in out


# --- (5) read_tickers_file edge cases ------------------------------------------
def test_tickers_file_missing_path_exits_nonzero(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        ab.read_tickers_file(str(tmp_path / "nonexistent.txt"))
    assert exc.value.code == 1
    assert "nonexistent.txt" in capsys.readouterr().out


def test_tickers_file_comment_lines_excluded(tmp_path: Path) -> None:
    listing = tmp_path / "tickers.txt"
    listing.write_text("# theme: AI 伺服器\n1111\n# 2222 disabled\n", encoding="utf-8")
    assert ab.read_tickers_file(str(listing)) == ["1111"]


# --- (6) empty resolved ticker list is an error, not Score: 0/0 ----------------
def test_empty_tickers_file_errors_nonzero(corpus: Path, tmp_path: Path, capsys) -> None:
    listing = tmp_path / "empty.txt"
    listing.write_text("# only a comment\n\n", encoding="utf-8")
    rc = ab.main(["--tickers-file", str(listing)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Score" not in out  # no vacuous 0/0 report
    assert "no tickers" in out.lower()
