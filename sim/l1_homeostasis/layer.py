"""L1 · 内感受 / 稳态层（10–100 ms）。

唯一职责：定义"正常"是什么、检测偏离，并广播。
它不规划、不理解语义、不认识充电桩——它只知道"电量 18%，偏了"。
"""

from __future__ import annotations

from .. import config as C
from ..contracts import HomeostaticBroadcast


class Homeostasis:
    def __init__(self, bus, log):
        self.bus = bus
        self.log = log
        self.battery = C.BATTERY_START
        self.temp = C.TEMP_AMBIENT
        self.stress = 0.0
        self.severity = 0.0
        self.cause: str | None = None
        self._acc = 0.0            # 采样累加器（L1 不需要每 tick 都广播）
        self._last_sig: HomeostaticBroadcast | None = None
        self.broadcasts = 0

    # ------------------------------------------------------------ 采样
    def tick(self, t: float, dt: float, speed: float, contact_force: float,
             charging: bool, decision_id: str) -> None:
        # 电量：移动耗电、静置轻耗、充电回升
        if charging:
            self.battery = min(100.0, self.battery + C.BATTERY_CHARGE * dt)
        else:
            move_share = min(1.0, speed / 1.35)
            self.battery -= (C.BATTERY_DRAIN_IDLE + C.BATTERY_DRAIN_MOVE * move_share) * dt
        self.battery = max(0.0, self.battery)

        # 温度：负荷产热、被动散热
        load = min(1.0, (speed / 1.35) * 0.7 + contact_force / max(1.0, C.STRESS_WARN) * 0.3)
        self.temp += (load * C.TEMP_PER_LOAD - C.TEMP_COOL * (self.temp - C.TEMP_AMBIENT)) * dt

        # 关节应力：这里用接触力当替身（真机应读电流/力矩传感器）。
        # 必须做低通：碰撞冲量是瞬时尖峰，直接当应力会让 L1 误报 thermal/joint 需求。
        self.stress = 0.85 * self.stress + 0.15 * contact_force

        cpu = self._severity_of()
        self.severity = max(cpu.values()) if cpu else 0.0
        self.cause = max(cpu, key=cpu.get) if cpu and self.severity > 0.01 else None

        # 按 10–100ms 的节奏广播（不是每 tick）
        self._acc += dt
        if self._acc >= C.SAMPLE_L1:
            self._acc = 0.0
            if self.severity > 0.02:
                sig = HomeostaticBroadcast(
                    severity=round(self.severity, 4),
                    cause=self.cause or "battery_low",
                    preemptible=True,
                    timestamp_ms=0,
                )
                self.broadcasts += 1
                self._last_sig = sig
                self.bus.emit(sig, "L1", decision_id, need=self.cause,
                              value=round(self.severity, 3),
                              detail=f"battery={self.battery:.1f}% temp={self.temp:.1f}C")

    def _severity_of(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if self.battery < C.BATTERY_LOW:
            out["battery_low"] = min(1.0, (C.BATTERY_LOW - self.battery) / C.BATTERY_LOW)
        if self.temp > C.TEMP_WARN:
            out["thermal_high"] = min(1.0, (self.temp - C.TEMP_WARN) / 30.0)
        if self.stress > C.STRESS_WARN:
            out["joint_stress"] = min(1.0, (self.stress - C.STRESS_WARN) / C.STRESS_WARN)
        return out

    @property
    def low_battery(self) -> bool:
        return self.battery < C.BATTERY_LOW

    def state(self) -> dict:
        return {
            "battery": round(self.battery, 1),
            "temp": round(self.temp, 1),
            "stress": round(self.stress, 1),
            "severity": round(self.severity, 2),
            "cause": self.cause,
        }
