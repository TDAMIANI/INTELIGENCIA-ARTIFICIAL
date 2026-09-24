import cv2
import numpy as np

from mvsim import cameras
from mvsim.conveyor import ConveyorSimulator, Fault, FaultKind


def test_thermal_frame_is_radiometric_and_shows_hot_idler() -> None:
    sim = ConveyorSimulator(seed=1)
    sim.inject(Fault(FaultKind.idler_bearing, target=5, ramp_s=0))
    for _ in range(100):
        snap = sim.step(10.0)
    frame = cameras.render_thermal(sim, snap, np.random.default_rng(0))
    assert frame.dtype == np.uint16 and frame.shape == (240, 320)
    temps = frame.astype(np.float32) / 100 - 273.15
    assert temps.max() == np.float32(temps[40:120, 64:80].max())  # polín 5: fila 0, columna 4
    assert abs(temps.max() - snap.idler_temps_c[4]) < 1.0
    decoded = cv2.imdecode(np.frombuffer(cameras.encode_thermal(frame), np.uint8), cv2.IMREAD_UNCHANGED)
    assert np.array_equal(decoded, frame)  # PNG sin pérdidas


def test_rgb_frame_moves_with_misalignment() -> None:
    sim = ConveyorSimulator(seed=2)
    rng = np.random.default_rng(0)
    centered = cameras.render_rgb(sim.step(1.0), rng)
    sim.inject(Fault(FaultKind.belt_misalignment, ramp_s=0, severity=1.0))
    shifted = cameras.render_rgb(sim.step(1.0), rng)
    assert centered.shape == (360, 640, 3)

    def dark_center(img):
        cols = np.flatnonzero(img.mean(axis=(0, 2)) < 80)
        return (cols[0] + cols[-1]) / 2

    assert dark_center(shifted) - dark_center(centered) > 20  # 80 mm / 3 mm/px ≈ 27 px
    assert cameras.encode_rgb(centered)[:2] == b"\xff\xd8"  # JPEG
