"""Small deterministic checks for the example's simulation, not runtime gates."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np

source = Path(__file__).resolve().parents[1] / "Sources/ParticleShowcase/Resources/particles.py"
spec = importlib.util.spec_from_file_location("showcase_particles", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ParticleSimulationTests(unittest.TestCase):
    def make_simulation(self):
        output = np.zeros((1024, 4), dtype=np.float32)
        glyph = np.zeros((16, 32), dtype=np.uint8)
        glyph[4:12, 8:24] = 255
        return output, module.ParticleSimulation(output, glyph)

    def test_seed_reproduces_motion_and_writes_the_supplied_tensor(self):
        output, first = self.make_simulation()
        _, second = self.make_simulation()
        before = output.copy()
        for simulation in (first, second):
            for i in range(40):
                simulation.step(1, i / 30, 1 / 30, 1)
        np.testing.assert_array_equal(output, second.output)
        self.assertIs(first.output, output)
        self.assertFalse(np.array_equal(before, output))
        self.assertTrue(np.isfinite(output).all())
        self.assertEqual(first.proof()["steps"], 40)

    def test_all_formations_recover_from_scatter_without_nonfinite_values(self):
        output, simulation = self.make_simulation()
        digests = set()
        for mode in range(4):
            for i in range(150):
                simulation.step(mode, i / 30, 1 / 30, mode + 1)
            self.assertTrue(np.isfinite(output).all())
            # After five seconds the damped particles have converged near
            # their targets, including recovery from the explicit impulse.
            self.assertLess(float(np.max(np.abs(output[:, :3] - simulation.target))), 0.6)
            digests.add(simulation.proof()["sha256"])
        self.assertEqual(len(digests), 4)

    def test_bad_step_is_rejected_before_mutation(self):
        output, simulation = self.make_simulation()
        before = output.copy()
        for args in [(4, 0, 1 / 30, 0), (0, float("nan"), 1 / 30, 0), (0, 0, 0, 0), (0, 0, 1, 0)]:
            with self.assertRaises(ValueError):
                simulation.step(*args)
        np.testing.assert_array_equal(output, before)
        self.assertEqual(simulation.step_number, 0)

    def test_top_bitmap_rows_become_positive_world_y(self):
        output = np.zeros((1024, 4), dtype=np.float32)
        mask = np.zeros((16, 32), dtype=np.uint8)
        mask[:4, 8:24] = 255
        simulation = module.ParticleSimulation(output, mask)
        self.assertTrue((simulation.formations[3][:, 1] > 0).all())


if __name__ == "__main__":
    unittest.main()
