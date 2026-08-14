import json
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_LIB = REPO_ROOT / "deploy" / "lib"
if str(DEPLOY_LIB) not in sys.path:
    sys.path.insert(0, str(DEPLOY_LIB))

from casbot_deploy.metadata_probe import (
    ProbeExternalShutdown,
    ProbeTimeout,
    extract_metadata,
    format_metadata,
    probe_first_metadata,
)


class GuardedMessage:
    sample_rate = 24_000
    channels = 1
    format = "phase8-observed-format"

    @property
    def data(self):
        raise AssertionError("audio payload must never be accessed")


class FakeRuntime:
    def __init__(self, behavior: str, *, initialized_here: bool = True) -> None:
        self.behavior = behavior
        self.initialized_here = initialized_here
        self.callback = None
        self.topic = None
        self.ok_value = True
        self.destroy_calls = 0
        self.shutdown_calls = 0
        self.spin_calls = 0

    def initialize(self) -> bool:
        return self.initialized_here

    def subscribe(self, topic: str, callback) -> None:
        self.topic = topic
        self.callback = callback

    def ok(self) -> bool:
        return self.ok_value

    def spin_once(self, timeout: float) -> None:
        self.spin_calls += 1
        if self.behavior == "message":
            self.callback(GuardedMessage())
        elif self.behavior == "external-shutdown":
            self.ok_value = False
            raise ProbeExternalShutdown("external shutdown")
        elif self.behavior == "keyboard":
            raise KeyboardInterrupt

    def destroy(self) -> None:
        self.destroy_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.ok_value = False


class AdvancingClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.06
        return self.value


class DeploymentMetadataProbeTest(unittest.TestCase):
    def test_first_frame_outputs_only_allowed_metadata_and_never_data(self) -> None:
        runtime = FakeRuntime("message")
        metadata = probe_first_metadata(
            runtime,
            topic="/lzdl10823/audio/dialog_play",
            timeout=1.0,
        )
        self.assertEqual(
            metadata,
            {
                "sample_rate": 24_000,
                "channels": 1,
                "format": "phase8-observed-format",
            },
        )
        self.assertEqual(runtime.topic, "/lzdl10823/audio/dialog_play")
        self.assertEqual(runtime.destroy_calls, 1)
        self.assertEqual(runtime.shutdown_calls, 1)
        rendered = format_metadata(metadata, json_output=False)
        self.assertNotIn("data", rendered)

    def test_extract_metadata_does_not_access_payload(self) -> None:
        self.assertEqual(extract_metadata(GuardedMessage())["channels"], 1)

    def test_json_output_has_exactly_three_fields(self) -> None:
        metadata = extract_metadata(GuardedMessage())
        payload = json.loads(format_metadata(metadata, json_output=True))
        self.assertEqual(set(payload), {"sample_rate", "channels", "format"})

    def test_timeout_is_non_success_and_cleans_owned_context(self) -> None:
        runtime = FakeRuntime("timeout")
        with self.assertRaisesRegex(ProbeTimeout, "timed out"):
            probe_first_metadata(
                runtime,
                topic="/lzdl10823/audio/dialog_play",
                timeout=0.1,
                monotonic=AdvancingClock(),
            )
        self.assertEqual(runtime.destroy_calls, 1)
        self.assertEqual(runtime.shutdown_calls, 1)

    def test_external_shutdown_does_not_call_shutdown_again(self) -> None:
        runtime = FakeRuntime("external-shutdown")
        with self.assertRaises(ProbeExternalShutdown):
            probe_first_metadata(
                runtime,
                topic="/lzdl10823/audio/dialog_play",
                timeout=1.0,
            )
        self.assertEqual(runtime.destroy_calls, 1)
        self.assertEqual(runtime.shutdown_calls, 0)

    def test_existing_context_is_never_shutdown_by_probe(self) -> None:
        runtime = FakeRuntime("message", initialized_here=False)
        probe_first_metadata(
            runtime,
            topic="/lzdl10823/audio/dialog_play",
            timeout=1.0,
        )
        self.assertEqual(runtime.destroy_calls, 1)
        self.assertEqual(runtime.shutdown_calls, 0)

    def test_keyboard_interrupt_still_destroys_and_safely_shuts_owned_context(self) -> None:
        runtime = FakeRuntime("keyboard")
        with self.assertRaises(KeyboardInterrupt):
            probe_first_metadata(
                runtime,
                topic="/lzdl10823/audio/dialog_play",
                timeout=1.0,
            )
        self.assertEqual(runtime.destroy_calls, 1)
        self.assertEqual(runtime.shutdown_calls, 1)


if __name__ == "__main__":
    unittest.main()
