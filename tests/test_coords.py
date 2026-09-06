from kiai_second_sight.coords import gtp_to_point, point_to_gtp


def test_gtp_round_trip_9x9():
    for point in [(0, 0), (3, 4), (8, 8)]:
        assert gtp_to_point(point_to_gtp(point, 9), 9) == point


def test_gtp_skips_i_column():
    assert point_to_gtp((0, 7), 9) == "H1"
    assert point_to_gtp((0, 8), 9) == "J1"
