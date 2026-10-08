from studio.gui.bridge import RunnerBridge


def test_bridge_constructs_without_starting_process() -> None:
    bridge = RunnerBridge()
    assert bridge.process.state().name == "NotRunning"
