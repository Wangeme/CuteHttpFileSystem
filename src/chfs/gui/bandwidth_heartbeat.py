"""可复用的实时带宽爱心动画。

组件只消费传输快照字典，不依赖 CHFS 的控制器或 HTTP 实现。外部程序只需定期
传入 ``id``、``direction``、``status`` 和 ``transferred_bytes``，即可用相邻
快照的字节差驱动动画；以后可以直接把本文件抽成独立素材。
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Iterable, Mapping


PINK = "#ff5c9f"
PINK_LIGHT = "#ffc1da"
PINK_GLOW = "#8f234f"
PINK_FILL = "#641536"
GRID_MINOR = "#123420"
GRID_MAJOR = "#1a4a2b"
BACKGROUND = "#052d15"


@dataclass(frozen=True, slots=True)
class HeartbeatState:
    """一次采样后供绘图层使用的不可变状态。"""

    mode: str
    bytes_per_second: float
    utilization: float
    beats_per_minute: float


class BandwidthHeartbeatModel:
    """把累计传输字节转换成平滑的实时带宽和心跳频率。"""

    ACTIVE_STATUSES = {
        "upload": {"uploading"},
        "download": {"downloading"},
    }

    def __init__(self, reference_bytes_per_second: float = 125 * 1024 * 1024) -> None:
        if reference_bytes_per_second <= 0:
            raise ValueError("reference_bytes_per_second 必须大于 0")
        self.reference_bytes_per_second = float(reference_bytes_per_second)
        self._last_sample_at: float | None = None
        self._previous_bytes: dict[str, int] = {}
        self._smoothed_rate = 0.0
        self.state = HeartbeatState("idle", 0.0, 0.0, 34.0)

    def sample(
        self,
        snapshots: Iterable[Mapping[str, object]],
        *,
        now: float | None = None,
    ) -> HeartbeatState:
        """采样传输快照；默认参考上限为千兆网络约 125 MiB/s。"""

        sampled_at = time.monotonic() if now is None else float(now)
        active: list[Mapping[str, object]] = []
        directions: set[str] = set()
        current_bytes: dict[str, int] = {}
        for snapshot in snapshots:
            direction = str(snapshot.get("direction", ""))
            status = str(snapshot.get("status", ""))
            if status not in self.ACTIVE_STATUSES.get(direction, set()):
                continue
            transfer_id = str(snapshot.get("id", ""))
            if not transfer_id:
                continue
            active.append(snapshot)
            directions.add(direction)
            current_bytes[transfer_id] = max(0, int(snapshot.get("transferred_bytes", 0)))

        elapsed = 0.0 if self._last_sample_at is None else max(0.0, sampled_at - self._last_sample_at)
        raw_rate = 0.0
        if elapsed > 0:
            delta = sum(
                max(0, transferred - self._previous_bytes[transfer_id])
                for transfer_id, transferred in current_bytes.items()
                if transfer_id in self._previous_bytes
            )
            raw_rate = delta / elapsed

        # 约 0.32 秒时间常数：足以消除分块上传带来的锯齿，又能紧跟真实加减速。
        alpha = 1.0 if self._last_sample_at is None else 1.0 - math.exp(-elapsed / 0.32)
        self._smoothed_rate += (raw_rate - self._smoothed_rate) * alpha
        if not active and self._smoothed_rate < 32 * 1024:
            self._smoothed_rate = 0.0

        if directions == {"upload", "download"}:
            mode = "mixed"
        elif "upload" in directions:
            mode = "upload"
        elif "download" in directions:
            mode = "download"
        else:
            mode = "idle"

        utilization = min(1.0, self._smoothed_rate / self.reference_bytes_per_second)
        # 活跃传输即使刚开始也应有可见反馈；接近千兆上限时提升到约 200 BPM。
        intensity = max(utilization, 0.08 if active else 0.0)
        bpm = 34.0 + 166.0 * (intensity**0.68)
        self.state = HeartbeatState(mode, self._smoothed_rate, utilization, bpm)
        self._previous_bytes = current_bytes
        self._last_sample_at = sampled_at
        return self.state

    def reset(self) -> HeartbeatState:
        """清除历史采样，回到低频呼吸状态。"""

        self._last_sample_at = None
        self._previous_bytes.clear()
        self._smoothed_rate = 0.0
        self.state = HeartbeatState("idle", 0.0, 0.0, 34.0)
        return self.state


class BandwidthHeartbeat(tk.Canvas):
    """粉色爱心带宽图；速率越高，爱心经过的频率越快。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        width: int = 512,
        height: int = 72,
        reference_bytes_per_second: float = 125 * 1024 * 1024,
        background: str = BACKGROUND,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=background,
            bd=0,
            highlightthickness=0,
        )
        self._background = background
        self._model = BandwidthHeartbeatModel(reference_bytes_per_second)
        self._running = False
        self._last_frame_at = time.monotonic()
        self._travel = 0.0
        self._draw_grid()
        self._animation_id = self.after(33, self._animate)

    @property
    def heartbeat_state(self) -> HeartbeatState:
        return self._model.state

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        if not running:
            self._model.reset()

    def update_transfers(self, snapshots: Iterable[Mapping[str, object]]) -> HeartbeatState:
        """输入最新累计字节快照并返回当前动画状态。"""

        return self._model.sample(snapshots)

    def destroy(self) -> None:
        if getattr(self, "_animation_id", None) is not None:
            self.after_cancel(self._animation_id)
            self._animation_id = None
        super().destroy()

    def _draw_grid(self) -> None:
        width = int(self.cget("width"))
        height = int(self.cget("height"))
        for x in range(0, width + 1, 4):
            color = GRID_MAJOR if x % 16 == 0 else GRID_MINOR
            self.create_line(x, 0, x, height, fill=color, tags=("grid",))
        for y in range(0, height + 1, 4):
            color = GRID_MAJOR if y % 16 == 0 else GRID_MINOR
            self.create_line(0, y, width, y, fill=color, tags=("grid",))

    def _animate(self) -> None:
        now = time.monotonic()
        elapsed = min(0.1, max(0.0, now - self._last_frame_at))
        self._last_frame_at = now
        if self._running:
            state = self._model.state
            # 横向速度也随负载上升；与可变间距共同表现“带宽越满，心跳越快”。
            pixels_per_second = 84.0 + state.utilization * 58.0
            self._travel = (self._travel + pixels_per_second * elapsed) % 10000.0
            self._render(state, now)
        else:
            self.delete("dynamic")
        self._animation_id = self.after(33, self._animate)

    def _render(self, state: HeartbeatState, now: float) -> None:
        self.delete("dynamic")
        width = int(self.cget("width"))
        height = int(self.cget("height"))
        middle = height / 2 + 2
        speed = 84.0 + state.utilization * 58.0
        spacing = max(38.0, speed * 60.0 / state.beats_per_minute)
        offset = self._travel % spacing

        line_color = PINK_LIGHT if state.mode == "upload" else PINK
        self.create_line(0, middle, width, middle, fill=PINK_GLOW, width=3, tags=("dynamic",))
        self.create_line(0, middle, width, middle, fill=line_color, width=1, tags=("dynamic",))

        pulse = 1.0 + 0.08 * math.sin(now * state.beats_per_minute * math.tau / 60.0)
        heart_size = (17.0 + state.utilization * 7.0) * pulse
        index = 0
        x = width + spacing - offset
        while x > -spacing:
            filled = state.mode in {"upload", "mixed"} and (state.mode != "mixed" or index % 2 == 0)
            self._draw_heart(x, middle - 1, heart_size, filled=filled, line_color=line_color)
            x -= spacing
            index += 1

        direction = {
            "upload": "↑ 上传",
            "download": "↓ 下载",
            "mixed": "↕ 双向",
            "idle": "♡ 待机",
        }[state.mode]
        rate = self._format_rate(state.bytes_per_second)
        self.create_text(
            width - 8,
            8,
            text=f"{direction}  {rate}",
            anchor="ne",
            fill=PINK_LIGHT,
            font=("Cascadia Mono", 8, "bold"),
            tags=("dynamic",),
        )

    def _draw_heart(
        self,
        center_x: float,
        center_y: float,
        size: float,
        *,
        filled: bool,
        line_color: str,
    ) -> None:
        points: list[float] = []
        for step in range(37):
            angle = math.tau * step / 36
            x = 16 * math.sin(angle) ** 3
            y = 13 * math.cos(angle) - 5 * math.cos(2 * angle) - 2 * math.cos(3 * angle) - math.cos(4 * angle)
            points.extend((center_x + x * size / 32, center_y - y * size / 32))
        self.create_polygon(
            points,
            fill=PINK_GLOW if filled else "",
            outline=PINK_GLOW,
            width=5,
            smooth=True,
            splinesteps=12,
            tags=("dynamic",),
        )
        self.create_polygon(
            points,
            fill=PINK_FILL if filled else self._background,
            outline=line_color,
            width=2,
            smooth=True,
            splinesteps=12,
            tags=("dynamic",),
        )

    @staticmethod
    def _format_rate(value: float) -> str:
        units = ("B/s", "KiB/s", "MiB/s", "GiB/s")
        amount = max(0.0, float(value))
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f"{amount:.1f} {unit}" if amount < 100 else f"{amount:.0f} {unit}"
            amount /= 1024
        return "0 B/s"
