import unittest

import common
from cogs import SquadQueue
from mogi_objects import Player, Mogi, Team
import random


class AlgorithmTests(unittest.TestCase):

    def test_empty_player_list(self):
        """Test for empty player list"""
        players = []
        num_players = 12
        result = Mogi._minimize_range(players, num_players)
        self.assertIsNone(result)

    def test_not_enough_players(self):
        """Test for when too many players are requested"""
        players = [Player(None, None, None), Player(None, None, None)]
        random.shuffle(players)
        num_players = 3
        result = Mogi._minimize_range(players, num_players)
        self.assertIsNone(result)

    def test_enough_players(self):
        """Test for when there are enough requested players"""
        players = [Player(None, "Joe", 100), Player(None, "Bob", 200)]
        random.shuffle(players)
        num_players = 2
        result = Mogi._minimize_range(players, num_players)
        self.assertIsNotNone(result)

    def test_one_possible_collection(self):
        """Test for when the number of players given is the number of players requested"""
        p1 = Player(None, "Joe", 100)
        p2 = Player(None, "Bob", 200)
        players = [p1, p2]
        random.shuffle(players)
        num_players = 2
        result = Mogi._minimize_range(players, num_players)
        self.assertListEqual([p1, p2], result)

    def test_correct_collection_1(self):
        """Test #1 for correctness"""
        p1 = Player(None, "Joe", 100)
        p2 = Player(None, "Bob", 200)
        p3 = Player(None, "Jane", 250)
        players = [p1, p2, p3]
        random.shuffle(players)
        num_players = 2
        result = Mogi._minimize_range(players, num_players)
        self.assertListEqual([p2, p3], result)

    def test_correct_collection_2(self):
        """Test #2 for correctness"""
        p1 = Player(None, "Joe", 100)
        p2 = Player(None, "Bob", 200)
        p3 = Player(None, "Jane", 350)
        players = [p1, p2, p3]
        random.shuffle(players)
        num_players = 2
        result = Mogi._minimize_range(players, num_players)
        self.assertListEqual([p1, p2], result)

    def test_correct_collection_3(self):
        """Test #3 for correctness"""
        p1 = Player(None, "Joe", 100)
        p2 = Player(None, "Bob", 0)
        p3 = Player(None, "Jane", 350)
        players = [p1, p2, p3]
        random.shuffle(players)
        num_players = 2
        result = Mogi._minimize_range(players, num_players)
        self.assertListEqual([p2, p1], result)

    def test_correct_collection_4(self):
        """Test #4 for correctness"""
        p1 = Player(None, "Joe", 0)
        p2 = Player(None, "Bob", -100)
        p3 = Player(None, "Jane", 1)
        players = [p1, p2, p3]
        random.shuffle(players)
        num_players = 2
        result = Mogi._minimize_range(players, num_players)
        self.assertListEqual([p1, p3], result)

    def test_correct_collection_5(self):
        """Test #5 for correctness"""
        p1 = Player(None, "Joe", 0)
        p2 = Player(None, "Bob", 100)
        p3 = Player(None, "Jane", 1)
        players = [p1, p2, p3]
        random.shuffle(players)
        num_players = 2
        result = Mogi._minimize_range(players, num_players)
        self.assertListEqual([p1, p3], result)

    def test_correct_collection_6(self):
        """Test #6 for correctness, large player collection:
        -300
        0
        100
        200
        500
        501
        502
        600
        750
        800
        1000
        1500    : 1800
        1501    : 1501
        1550    : 1450
        1800    : 1600
        2000    : 1500"""
        p1 = Player(None, "1", 1000)
        p2 = Player(None, "2", 500)
        p3 = Player(None, "3", 1500)
        p4 = Player(None, "4", 750)
        p5 = Player(None, "5", 800)
        p6 = Player(None, "6", 2000)
        p7 = Player(None, "7", 0)
        p8 = Player(None, "8", -300)
        p9 = Player(None, "9", 501)
        p10 = Player(None, "10", 600)
        p11 = Player(None, "11", 200)
        p12 = Player(None, "12", 1550)
        p13 = Player(None, "13", 1800)
        p14 = Player(None, "14", 1501)
        p15 = Player(None, "15", 502)
        p16 = Player(None, "16", 100)
        players = [p1, p2, p3, p4, p5, p6, p7, p8,
                   p9, p10, p11, p12, p13, p14, p15, p16]
        random.shuffle(players)
        num_players = 12
        result = Mogi._minimize_range(players, num_players)
        self.assertListEqual(
            [p16, p11, p2, p9, p15, p10, p4, p5, p1, p3, p14, p12], result)

    def test_correct_collection_7(self):
        """Test #7 for large player collection where 12 players are given and 12 are requested"""
        p1 = Player(None, "1", 1000)
        p2 = Player(None, "2", 500)
        p3 = Player(None, "3", 1500)
        p4 = Player(None, "4", 750)
        p5 = Player(None, "5", 800)
        p6 = Player(None, "6", 2000)
        p7 = Player(None, "7", 0)
        p8 = Player(None, "8", -300)
        p9 = Player(None, "9", 500)
        p10 = Player(None, "10", 600)
        p11 = Player(None, "11", 200)
        p12 = Player(None, "12", 1550)
        players = [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12]
        random.shuffle(players)
        num_players = 12
        result = Mogi._minimize_range(players, num_players)
        self.assertSetEqual(set(players), set(result))


class OneRoomAlgorithmTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.threshold = 65
        cls.threshold_function = None
        if common.SERVER is common.Server.MKW:
            cls.threshold_function = lambda players: SquadQueue.mkw_players_allowed(
                players, cls.threshold)
        elif common.SERVER is common.Server.MK8DX:
            cls.threshold_function = lambda players: SquadQueue.mk8dx_players_allowed(
                players, cls.threshold)
        else:
            raise Exception(
                "Lounge parameter in config is invalid, unit tests will fail.")

    def _new_default_mogi(self) -> Mogi:
        """Returns a default mogi with 12 players per room."""
        self  # to hide annoying PyCharm IDE error - well aware it could be static, do not want it to be
        return Mogi(sq_id=1,
                    max_players_per_team=1,
                    players_per_room=12,
                    mogi_channel=None
                    )

    def test_no_players(self):
        """Test when no players are queued"""
        mogi = self._new_default_mogi()
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertListEqual([], results)
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_INSUFFICIENT_PLAYERS)

    def test_one_player(self):
        """Test when one player is queued"""
        p1 = Player(None, "1", 1000, True)
        mogi = self._new_default_mogi()
        mogi.teams.append(Team([p1]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertListEqual([], results)
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_INSUFFICIENT_PLAYERS)

    def test_two_players(self):
        """Test when two players are queued"""
        p1 = Player(None, "1", 1000, True)
        p2 = Player(None, "2", 2000, True)
        mogi = self._new_default_mogi()
        mogi.teams.append(Team([p1]))
        mogi.teams.append(Team([p2]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertListEqual([], results)
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_INSUFFICIENT_PLAYERS)

    def test_large_max_players_per_team(self):
        """This is to catch the Mogi class computes the number of confirmed players incorrectly"""
        p1 = Player(None, "1", 1000, True)
        mogi = self._new_default_mogi()
        mogi.max_player_per_team = 24
        mogi.teams.append(Team([p1]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertListEqual([], results)
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_INSUFFICIENT_PLAYERS)

    def test_2_rooms_correctness_1(self):
        """Test if 24P for 12 player rooms generates 2 rooms with correct status code"""
        players = [Player(None, f"{i}", i, True) for i in range(24)]
        mogi = self._new_default_mogi()
        for p in players:
            mogi.teams.append(Team([p]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertSetEqual(set(players), set(results))
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_2_OR_MORE_ROOMS)

    def test_2_rooms_correctness_2(self):
        """Test if 25P for 12 player rooms generates 2 rooms with correct players and correct status code"""
        players = [Player(None, f"{i}", i, True) for i in range(25)]
        random.shuffle(players)
        mogi = self._new_default_mogi()
        for p in players:
            mogi.teams.append(Team([p]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertSetEqual(set(players[0:24]), set(results))
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_2_OR_MORE_ROOMS)

    def test_2_rooms_correctness_3(self):
        """Test if 25P for 12 player rooms generates 2 rooms with correct players and correct status code even with extremely large range"""
        players = [
            Player(
                None,
                f"{i*10000}",
                i * 10000,
                True) for i in range(25)]
        random.shuffle(players)
        mogi = self._new_default_mogi()
        for p in players:
            mogi.teams.append(Team([p]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertSetEqual(set(players[0:24]), set(results))
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_2_OR_MORE_ROOMS)

    def test_1_room_correctness_1(self):
        """Test if 15P for 12 player rooms generates 1 rooms with correct players and correct status code"""
        assert self.threshold == 65
        # -64, -49, -36, ... 0, 1, 4, ... 64
        players = [Player(None, f"{i}", abs(
            i - 8) * (i - 8), True) for i in range(17)]
        # Set 36 rating to not confirmed so only one possibility occurs
        players.pop(15)
        # Only one possibility that matches threshold
        correct_result = players[2:14].copy()
        random.shuffle(players)
        mogi = self._new_default_mogi()
        for p in players:
            mogi.teams.append(Team([p]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_SUCCESS_FOUND)
        self.assertSetEqual(set(correct_result), set(results))

    def test_2_room_correctness_2(self):
        """Test if 15P for 12 player rooms does not find any rooms when players are beyond range"""
        assert self.threshold == 65
        # -512, -343, ... 0, 1, 8, ... 512
        players = [Player(None, f"{i}", (i - 8)**3, True) for i in range(17)]
        correct_result = []  # No possible list of players
        random.shuffle(players)
        mogi = self._new_default_mogi()
        for p in players:
            mogi.teams.append(Team([p]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_SUCCESS_EMPTY)
        self.assertSetEqual(set(correct_result), set(results))

    def test_2_room_correctness_3(self):
        """Test if 15P for 12 player rooms finds the single correct solution"""
        assert self.threshold == 65
        p1 = Player(None, "1", 10, True)
        p2 = Player(None, "2", 5, True)
        p3 = Player(None, "3", 14, True)
        p4 = Player(None, "4", 75, True)
        p5 = Player(None, "5", 8, True)
        p6 = Player(None, "6", 20, True)
        p7 = Player(None, "7", 0, True)
        p8 = Player(None, "8", -3, True)
        p9 = Player(None, "9", 4, True)
        p10 = Player(None, "10", -105, True)
        p11 = Player(None, "11", 2, True)
        p12 = Player(None, "12", 15, True)
        p13 = Player(None, "13", 16, True)
        p14 = Player(None, "14", 74, True)
        p15 = Player(None, "15", 9, True)
        p16 = Player(None, "16", 100, True)
        # p4 should be removed, p10 should be removed, p13 should be added, p15
        # should be added
        players = [p1, p2, p3, p4, p5, p6, p7, p8,
                   p9, p10, p11, p12, p13, p14, p15, p16]
        correct_result = [p1, p2, p3, p5, p6, p7, p8, p9, p11, p12, p13, p15]
        mogi = self._new_default_mogi()
        for p in players:
            mogi.teams.append(Team([p]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_SUCCESS_FOUND)
        self.assertSetEqual(set(correct_result), set(results))

    def test_2_room_correctness_4(self):
        """Test if 15P for 12 player rooms finds no solution"""
        assert self.threshold == 65
        p1 = Player(None, "1", 10, True)
        p2 = Player(None, "2", 5, True)
        p3 = Player(None, "3", 14, True)
        p4 = Player(None, "4", 75, True)
        p5 = Player(None, "5", 8, True)
        p6 = Player(None, "6", 20, True)
        p7 = Player(None, "7", 0, True)
        p8 = Player(None, "8", -3, True)
        p9 = Player(None, "9", 4, True)
        p10 = Player(None, "10", -105, True)
        p11 = Player(None, "11", 2, True)
        p12 = Player(None, "12", 15, True)
        p13 = Player(None, "13", 16, True)
        p14 = Player(None, "14", 74, True)
        p15 = Player(None, "15", 9, False)
        p16 = Player(None, "16", 100, True)
        players = [p1, p2, p3, p4, p5, p6, p7, p8,
                   p9, p10, p11, p12, p13, p14, p15, p16]
        # Same as previous test, but since p15 is not confirmed, no result is
        # found
        correct_result = []
        mogi = self._new_default_mogi()
        for p in players:
            mogi.teams.append(Team([p]))
        results, status_code = mogi._one_room_final_list_algorithm(
            OneRoomAlgorithmTests.threshold_function)
        self.assertEqual(
            status_code,
            SquadQueue.Mogi.ALGORITHM_STATUS_SUCCESS_EMPTY)
        self.assertSetEqual(set(correct_result), set(results))


if __name__ == "__main__":
    """If PyCharm, add --verbose flag to configuration.
    Other IDEs, run from shell: python -m unittest ./unit_testing.py --verbose"""
    unittest.main(verbosity=2)
