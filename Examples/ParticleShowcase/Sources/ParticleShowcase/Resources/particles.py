"""Procedural spring particles. NumPy owns the motion; Metal only draws it.

No N-body claim: each particle follows a formation target, with damped velocity.
The output ndarray is the runtime's shared tensor, supplied by Swift.
"""
import hashlib
import os
import time
import numpy as np


class ParticleSimulation:
    def __init__(self, output, glyph_mask, seed=20260905):
        if output.dtype != np.float32 or output.ndim != 2 or output.shape[1] != 4:
            raise ValueError("output must be an N x 4 float32 tensor")
        if len(output) < 1024 or len(output) > 1048576:
            raise ValueError("particle count must be 1024...1048576")
        if glyph_mask.ndim != 2 or not np.any(glyph_mask > 128):
            raise ValueError("a nonempty two-dimensional glyph mask is required")
        self.output = output
        self.count = n = len(output)
        self.rng = rng = np.random.default_rng(seed)
        u = rng.random(n, dtype=np.float32)
        v = rng.random(n, dtype=np.float32)
        arm = (np.arange(n) % 4).astype(np.float32)
        radius = (0.06 + 3.15 * np.sqrt(u)).astype(np.float32)
        angle = arm * np.float32(np.pi / 2) + radius * 1.9
        angle += rng.normal(0, 0.13, n).astype(np.float32)
        galaxy = np.empty((n, 3), dtype=np.float32)
        galaxy[:, 0] = radius * np.cos(angle)
        galaxy[:, 1] = radius * np.sin(angle)
        galaxy[:, 2] = rng.normal(0, 0.09, n) * (1.15 - u)

        helix = np.empty_like(galaxy)
        phase = u * np.float32(5 * np.pi) + (arm % 2) * np.float32(np.pi)
        thickness = rng.normal(0, 0.075, n).astype(np.float32)
        helix[:, 0] = u * 6.2 - 3.1
        helix[:, 1] = np.cos(phase) * (0.95 + thickness)
        helix[:, 2] = np.sin(phase) * (0.95 + thickness)

        wave = np.empty_like(galaxy)
        wave[:, 0] = (u - 0.5) * 6.6
        wave[:, 1] = (v - 0.5) * 3.8
        wave[:, 2] = np.sin(wave[:, 0] * 2) * np.cos(wave[:, 1] * 2) * 0.55

        rows, columns = np.nonzero(glyph_mask > 128)
        picked = rng.integers(0, len(rows), n)
        height, width = glyph_mask.shape
        letters = np.empty_like(galaxy)
        letters[:, 0] = (columns[picked] + rng.random(n)) / width * 7.2 - 3.6
        # Bitmap rows begin at the top; the Metal world has positive Y up.
        letters[:, 1] = 1.8 - (rows[picked] + rng.random(n)) / height * 3.6
        letters[:, 2] = rng.normal(0, 0.012, n)

        self.formations = (galaxy, helix, wave, letters)
        self.colors = (
            1 - u,
            (arm % 2) * np.float32(0.9) + np.float32(0.05),
            u,
            (letters[:, 1] > 0).astype(np.float32) * 0.9 + 0.05,
        )
        self.target = np.empty_like(galaxy)
        self.velocity = np.zeros_like(galaxy)
        self.force = np.empty_like(galaxy)
        self.output[:, :3] = galaxy
        self.output[:, 3] = self.colors[0]
        self.step_number = 0
        self.last_burst = 0
        self.mode = 0

    def step(self, mode, seconds, dt, burst):
        if mode not in range(4) or not np.isfinite(seconds) or not 0 < dt <= 0.05:
            raise ValueError("invalid simulation step")
        started = time.perf_counter_ns()
        if mode != self.mode:
            self.output[:, 3] = self.colors[mode]
            self.mode = mode
        base = self.formations[mode]
        self.target[:] = base
        if mode == 0:
            angle = seconds * 0.12
            c, s = np.float32(np.cos(angle)), np.float32(np.sin(angle))
            self.target[:, 0] = base[:, 0] * c - base[:, 1] * s
            self.target[:, 1] = base[:, 0] * s + base[:, 1] * c
        elif mode == 2:
            self.target[:, 2] = (
                np.sin(base[:, 0] * 2 + seconds * 1.3)
                * np.cos(base[:, 1] * 2 - seconds * 0.7) * 0.65
            )
        if burst != self.last_burst:
            # One explicit UI impulse. It does not allocate a new shared tensor.
            self.velocity += self.rng.normal(0, 5.5, self.velocity.shape).astype(np.float32)
            self.last_burst = burst
        np.subtract(self.target, self.output[:, :3], out=self.force)
        self.force *= np.float32(30 * dt)
        self.velocity += self.force
        self.velocity *= np.float32(np.exp(-6 * dt))
        self.force[:] = self.velocity
        self.force *= np.float32(dt)
        self.output[:, :3] += self.force
        self.step_number += 1
        milliseconds = (time.perf_counter_ns() - started) / 1e6
        return [milliseconds, float(self.step_number)]

    def proof(self):
        words = self.output.view(np.uint32).reshape(-1)
        indices = [0, self.count // 2, self.count - 1]
        return {
            "pid": os.getpid(),
            "particles": self.count,
            "bytes": self.output.nbytes,
            "dtype": str(self.output.dtype),
            "finite": bool(np.isfinite(self.output).all()),
            "sha256": hashlib.sha256(memoryview(self.output)).hexdigest(),
            "sample_words": [int(words[i * 4 + j]) for i in indices for j in range(4)],
            "steps": self.step_number,
        }
