import unittest

from build123d import Axis, Box, Location


class DocumentedBuild123dTransformSemanticsTests(unittest.TestCase):
    def test_located_replaces_location_while_moved_composes_it(self) -> None:
        box = Box(10, 8, 4)
        placed = box.located(Location((0, 0, 0), (0, 0, 90)))

        replaced = placed.located(Location((5, 0, 0)))
        composed = placed.moved(Location((5, 0, 0)))

        self.assertAlmostEqual(10.0, replaced.bounding_box().size.X, places=5)
        self.assertAlmostEqual(8.0, composed.bounding_box().size.X, places=5)

    def test_rotate_transforms_geometry_and_survives_located(self) -> None:
        rotated = Box(10, 8, 4).rotate(Axis.Z, 90)
        relocated = rotated.located(Location((5, 0, 0)))

        self.assertAlmostEqual(8.0, relocated.bounding_box().size.X, places=5)
        self.assertAlmostEqual(10.0, relocated.bounding_box().size.Y, places=5)


if __name__ == "__main__":
    unittest.main()
