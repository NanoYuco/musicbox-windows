#!/usr/bin/python
import unicodedata
from time import time


def _units(text):
    """Keep a base character and its combining marks together when scrolling."""
    units = []
    for char in text:
        regional = 0x1F1E6 <= ord(char) <= 0x1F1FF
        if units and (
            unicodedata.combining(char)
            or unicodedata.category(char) in {"Mn", "Me", "Cf"}
            or units[-1].endswith("\u200d")
            or (
                regional
                and len(units[-1]) == 1
                and 0x1F1E6 <= ord(units[-1]) <= 0x1F1FF
            )
        ):
            units[-1] += char
        else:
            units.append(char)
    return units


class scrollstring:
    def __init__(self, content, START):
        self.content = content  # the true content of the string
        self.display = content  # the displayed string
        self.START = START // 1  # when this instance is created
        self.update()

    def update(self):
        units = _units(self.content)
        if not units:
            self.display = ""
            return
        curTime = time() // 1
        offset = max(int((curTime - self.START) % len(units)) - 1, 0)
        self.display = "".join(units[offset:] + units[:offset])

        # self.display = self.content[offset:] + self.content[:offset]

    def __repr__(self):
        return self.display


# determine the display length of a string


def truelen(string):
    """
    It appears one Asian character takes two spots, but __len__
    counts it as three, so this function counts the dispalyed
    length of the string.

    >>> truelen('abc')
    3
    >>> truelen('你好')
    4
    >>> truelen('1二3')
    4
    >>> truelen('')
    0
    """
    total = 0
    for unit in _units(string):
        widths = [
            0
            if unicodedata.category(c) in {"Mn", "Me", "Cf"}
            else 2
            if unicodedata.east_asian_width(c) in {"W", "F"}
            else 1
            for c in unit
        ]
        if "\ufe0f" in unit or (
            len(unit) == 2 and all(0x1F1E6 <= ord(c) <= 0x1F1FF for c in unit)
        ):
            total += 2
        else:
            total += max(widths, default=0)
    return total


def truelen_cut(string, length):
    current_length = 0
    result = []
    for unit in _units(string):
        current_length += truelen(unit)
        if current_length > length:
            break
        result.append(unit)
    return "".join(result)
