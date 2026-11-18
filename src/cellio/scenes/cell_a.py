from .definition import Box, Scene

SCENE = Scene(
    "cell_a",
    (Box("Conveyor/Belt", (.45, -.22, .77), (.14, .50, .04), "belt"),
     Box("Destination/Base", (.45, .25, .77), (.14, .14, .04))),
    (Box("PickupBeam", (.45, -.099, .81), (.10, .002, .04)),
     Box("Destination", (.45, .25, .815), (.10, .10, .060))),
    (.45, -.41, .815),
    {"park": (.30, 0., 1.10), "pickup_hover": (.45, -.116, 1.030),
     "pickup_grasp": (.45, -.116, .815), "transfer_mid": (.45, .08, 1.030),
     "output_hover": (.45, .25, 1.030), "output_place": (.45, .25, .815)},
    "Conveyor/Belt",
)
