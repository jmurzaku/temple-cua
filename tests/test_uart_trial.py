import importlib.util
from pathlib import Path
import sys


scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("run_uart_trial", scripts / "run_uart_trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def test_window_borders_do_not_hide_real_output():
    assert trial.standalone_marker_visible('\ufffdUART_UI_ABC123   \ufffd\nB:/Bench>', 'UART_UI_ABC123')


def test_command_echo_does_not_count_as_executed_output():
    assert not trial.standalone_marker_visible('\ufffdB:/Bench>"UART_UI_ABC123\\n";', 'UART_UI_ABC123')


def test_unknown_glyph_within_marker_does_not_count():
    assert not trial.standalone_marker_visible('\ufffdUART_UI_AB\ufffd123', 'UART_UI_ABC123')
