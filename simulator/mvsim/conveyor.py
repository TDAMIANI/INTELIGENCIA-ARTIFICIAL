"""Modelo simplificado de una correa transportadora con fallas inyectables.

No pretende ser un modelo físico exacto: genera señales con la forma y los órdenes
de magnitud realistas para desarrollar la ingesta, el motor del gemelo y el dashboard.

Señales generadas por paso:
- PLC: marcha/parada, velocidad de banda, corriente del motor, carga (t/h)
- Vibración del motor (mm/s RMS, rangos de ISO 10816 clase III)
- Temperatura máxima por polín (lo que publicaría el edge tras analizar la termografía)
- Desplazamiento del borde de la banda (lo que publicaría el edge tras analizar el video RGB)
"""

import math
import random
from dataclasses import dataclass, field
from enum import Enum


class FaultKind(str, Enum):
    idler_bearing = "idler_bearing"  # polín con rodamiento trabado -> se calienta
    motor_imbalance = "motor_imbalance"  # desbalance -> sube la vibración
    belt_misalignment = "belt_misalignment"  # la banda se corre hacia un lado


@dataclass
class Fault:
    kind: FaultKind
    start_s: float = 0.0
    # Segundos hasta alcanzar la severidad completa (degradación progresiva).
    ramp_s: float = 600.0
    severity: float = 1.0  # 0..1
    # Para idler_bearing: número de polín (1..N).
    target: int | None = None

    def level(self, t: float) -> float:
        """Severidad efectiva en el instante t (0 antes de empezar, rampa lineal después)."""
        if t < self.start_s:
            return 0.0
        if self.ramp_s <= 0:
            return self.severity
        return self.severity * min(1.0, (t - self.start_s) / self.ramp_s)


@dataclass
class ConveyorConfig:
    code: str = "CV-201"
    idler_count: int = 40
    nominal_speed_mps: float = 3.5
    acceleration_mps2: float = 0.25
    design_capacity_tph: float = 1500.0
    rated_current_a: float = 95.0
    ambient_c: float = 18.0
    # Calentamiento normal de un polín sano a plena velocidad.
    idler_rise_c: float = 12.0
    # Calentamiento adicional de un polín con falla a severidad 1.
    idler_fault_rise_c: float = 60.0
    # Constante de tiempo térmica de un polín (s).
    idler_tau_s: float = 180.0
    # Vibración de un motor sano a plena velocidad (zona A de ISO 10816 clase III: < 2,3 mm/s).
    base_vibration_mm_s: float = 1.6
    # Vibración adicional por desbalance a severidad 1 (entra en zona D: > 7,1 mm/s).
    imbalance_vibration_mm_s: float = 7.5
    max_misalignment_mm: float = 80.0


@dataclass
class Snapshot:
    t: float
    running: bool
    belt_speed_mps: float
    load_tph: float
    motor_current_a: float
    vibration_rms_mm_s: float
    idler_temps_c: list[float]
    belt_edge_offset_mm: float


@dataclass
class ConveyorSimulator:
    config: ConveyorConfig = field(default_factory=ConveyorConfig)
    seed: int | None = None

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.t = 0.0
        self.running = True
        self.speed = self.config.nominal_speed_mps
        self.load_frac = 0.7
        # Cada polín tiene un pequeño sesgo propio para que no sean todos iguales.
        self._idler_bias = [self.rng.gauss(0, 1.0) for _ in range(self.config.idler_count)]
        self.idler_temps = [
            self.config.ambient_c + self.config.idler_rise_c + b for b in self._idler_bias
        ]
        self.faults: list[Fault] = []

    # --- control --------------------------------------------------------------------------

    def inject(self, fault: Fault) -> None:
        if fault.kind is FaultKind.idler_bearing:
            if fault.target is None or not 1 <= fault.target <= self.config.idler_count:
                raise ValueError(f"idler_bearing requiere target entre 1 y {self.config.idler_count}")
        self.faults.append(fault)

    def clear_faults(self, kind: FaultKind | None = None) -> None:
        self.faults = [f for f in self.faults if kind is not None and f.kind is not kind]

    def set_running(self, running: bool) -> None:
        self.running = running

    def idler_code(self, n: int) -> str:
        return f"{self.config.code}-IDL-{n:02d}"

    # --- simulación -----------------------------------------------------------------------

    def _fault_level(self, kind: FaultKind, target: int | None = None) -> float:
        return max(
            (f.level(self.t) for f in self.faults if f.kind is kind and (target is None or f.target == target)),
            default=0.0,
        )

    def step(self, dt: float) -> Snapshot:
        cfg = self.config
        self.t += dt

        # Velocidad: rampa de arranque/parada.
        target_speed = cfg.nominal_speed_mps if self.running else 0.0
        max_delta = cfg.acceleration_mps2 * dt
        self.speed += max(-max_delta, min(max_delta, target_speed - self.speed))
        speed_frac = self.speed / cfg.nominal_speed_mps

        # Carga: paseo aleatorio acotado; sin marcha no hay carga.
        self.load_frac = min(1.0, max(0.3, self.load_frac + self.rng.gauss(0, 0.01) * math.sqrt(dt)))
        load_tph = cfg.design_capacity_tph * self.load_frac * speed_frac

        # Corriente: vacío (~30 %) + proporcional a la carga; cero si está detenido.
        current = 0.0
        if self.speed > 0.01:
            current = cfg.rated_current_a * (0.3 + 0.6 * self.load_frac) * speed_frac
            current += self.rng.gauss(0, 0.8)

        # Vibración del motor.
        imbalance = self._fault_level(FaultKind.motor_imbalance)
        vibration = speed_frac * (cfg.base_vibration_mm_s + imbalance * cfg.imbalance_vibration_mm_s)
        vibration = max(0.0, vibration + self.rng.gauss(0, 0.08) * speed_frac)

        # Temperatura de polines: primer orden hacia la temperatura objetivo.
        alpha = 1.0 - math.exp(-dt / cfg.idler_tau_s)
        for i in range(cfg.idler_count):
            fault = self._fault_level(FaultKind.idler_bearing, target=i + 1)
            target = (
                cfg.ambient_c
                + self._idler_bias[i]
                + speed_frac * (cfg.idler_rise_c + fault * cfg.idler_fault_rise_c)
            )
            self.idler_temps[i] += alpha * (target - self.idler_temps[i])
        temps = [round(temp + self.rng.gauss(0, 0.3), 1) for temp in self.idler_temps]

        # Desalineamiento: el borde se corre y oscila un poco.
        misalign = self._fault_level(FaultKind.belt_misalignment)
        offset = misalign * cfg.max_misalignment_mm + 4.0 * math.sin(self.t / 20.0) + self.rng.gauss(0, 1.5)
        offset *= 1.0 if self.speed > 0.01 else 0.0

        return Snapshot(
            t=self.t,
            running=self.running,
            belt_speed_mps=round(self.speed, 3),
            load_tph=round(load_tph, 1),
            motor_current_a=round(max(0.0, current), 1),
            vibration_rms_mm_s=round(vibration, 2),
            idler_temps_c=temps,
            belt_edge_offset_mm=round(offset, 1),
        )
