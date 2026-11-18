"""A prismatic jaw seats the part against a fixed jaw before a virtual dwell."""

from .definition import Box, Scene

SCENE = Scene(
    "cell_b",
    (Box("Source/Base", (.45, -.24, .77), (.14, .14, .04)),
     Box("Fixture/Base", (.50, .12, .77), (.18, .16, .04)),
     Box("Fixture/FixedJaw", (.47, .12, .82), (.02, .08, .06), "fixed_jaw"),
     Box("Fixture/MovingJaw", (.59, .12, .82), (.02, .08, .06), "moving_jaw", .25),
     Box("Destination/Base", (.35, .30, .77), (.14, .14, .04))),
    (Box("Source", (.45, -.24, .815), (.10, .10, .060)),
     Box("FixtureLoad", (.507, .12, .815), (.056, .056, .060)),
     Box("FixtureSeat", (.50, .12, .81), (.046, .056, .048)),
     Box("Destination", (.35, .30, .815), (.10, .10, .060))),
    (.45, -.24, .815),
    {"park": (.30, 0., 1.10), "source_hover": (.45, -.24, 1.030),
     "source_grasp": (.45, -.24, .815), "fixture_load_hover": (.507, .12, 1.030),
     "fixture_load_place": (.507, .12, .815), "fixture_unload_hover": (.500, .12, 1.030),
     "fixture_unload_grasp": (.500, .12, .815), "output_hover": (.35, .30, 1.030),
     "output_place": (.35, .30, .815)},
)
