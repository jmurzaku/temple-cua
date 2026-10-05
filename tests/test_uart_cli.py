from pathlib import Path

import pytest

from temple_cua.cli import main


TASKS = Path(__file__).resolve().parents[1] / "tasks"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "scripted"])
def test_uart_rejects_providers_without_live_device_grading_before_startup(provider, capsys):
    with pytest.raises(SystemExit) as failure:
        main(["run", "--tasks", str(TASKS), "--task", "uart_irq_service", "--provider", provider])
    assert failure.value.code == 2
    assert "require --provider cua" in capsys.readouterr().err


def test_uart_rejects_incompatible_snapshot_before_model_or_vm_startup(capsys):
    with pytest.raises(SystemExit) as failure:
        main(["run", "--tasks", str(TASKS), "--task", "uart_irq_service",
              "--provider", "cua", "--baseline", "does-not-exist.qcow2"])
    assert failure.value.code == 2
    assert "fresh boot with COM1" in capsys.readouterr().err
