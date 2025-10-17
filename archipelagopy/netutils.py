import typing


class NetworkItem(typing.NamedTuple):
    item: int
    location: int
    player: int
    """ Sending player, except in LocationInfo (from LocationScouts), where it is the receiving player. """
    flags: int = 0
