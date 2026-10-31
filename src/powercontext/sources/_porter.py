# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Dependency-free English stemming for PDF page retrieval.

Financial filings ask about the same fact with different word forms -- a question
says *geographies* / *liabilities* / *operating* while the page says *geographic* /
*liability* / *operations*.  Exact token matching then reports no overlap at all,
which was a leading cause of appendix-A misses on the proxy and public sets.

:func:`porter_stem` is the 1980 Porter suffix-stripping algorithm (M. F. Porter,
"An algorithm for suffix stripping", Program 14(3), 1980).  It is deterministic,
pure-Python, and needs no model or network.  A trailing ``i`` produced by the
``-ies -> -i`` rule is folded back so ``geographies`` and ``geographic`` conflate
to the same key.
"""

from __future__ import annotations

_VOWELS = frozenset("aeiou")


def _is_consonant(word: str, index: int) -> bool:
    character = word[index]
    if character in _VOWELS:
        return False
    if character == "y":
        return index == 0 or not _is_consonant(word, index - 1)
    return True


def _measure(stem: str) -> int:
    """Return Porter's ``m``: the number of vowel-consonant sequences in ``stem``."""

    count = 0
    index = 0
    length = len(stem)
    while index < length and _is_consonant(stem, index):
        index += 1
    while index < length:
        while index < length and not _is_consonant(stem, index):
            index += 1
        if index >= length:
            break
        count += 1
        while index < length and _is_consonant(stem, index):
            index += 1
    return count


def _contains_vowel(stem: str) -> bool:
    return any(not _is_consonant(stem, index) for index in range(len(stem)))


def _ends_double_consonant(word: str) -> bool:
    return len(word) >= 2 and word[-1] == word[-2] and _is_consonant(word, len(word) - 1)


def _ends_cvc(word: str) -> bool:
    if len(word) < 3:
        return False
    return (
        _is_consonant(word, len(word) - 3)
        and not _is_consonant(word, len(word) - 2)
        and _is_consonant(word, len(word) - 1)
        and word[-1] not in "wxy"
    )


_STEP2 = (
    ("ational", "ate"),
    ("tional", "tion"),
    ("enci", "ence"),
    ("anci", "ance"),
    ("izer", "ize"),
    ("bli", "ble"),
    ("alli", "al"),
    ("entli", "ent"),
    ("eli", "e"),
    ("ousli", "ous"),
    ("ization", "ize"),
    ("ation", "ate"),
    ("ator", "ate"),
    ("alism", "al"),
    ("iveness", "ive"),
    ("fulness", "ful"),
    ("ousness", "ous"),
    ("aliti", "al"),
    ("iviti", "ive"),
    ("biliti", "ble"),
    ("logi", "log"),
)
_STEP3 = (
    ("icate", "ic"),
    ("ative", ""),
    ("alize", "al"),
    ("iciti", "ic"),
    ("ical", "ic"),
    ("ful", ""),
    ("ness", ""),
)
_STEP4 = (
    "al",
    "ance",
    "ence",
    "er",
    "ic",
    "able",
    "ible",
    "ant",
    "ement",
    "ment",
    "ent",
    "ion",
    "ou",
    "ism",
    "ate",
    "iti",
    "ous",
    "ive",
    "ize",
)


def _replace_suffix(word: str, suffixes) -> str:
    for suffix, replacement in suffixes:
        if word.endswith(suffix) and _measure(word[: -len(suffix)]) > 0:
            return word[: -len(suffix)] + replacement
    return word


def porter_stem(word: str) -> str:
    """Return the Porter stem of one lowercase token."""

    if len(word) <= 2:
        return word
    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("ies"):
        word = word[:-2]
    elif word.endswith("ss"):
        pass
    elif word.endswith("s"):
        word = word[:-1]

    flag = False
    if word.endswith("eed"):
        if _measure(word[:-3]) > 0:
            word = word[:-1]
    elif word.endswith("ed") and _contains_vowel(word[:-2]):
        word = word[:-2]
        flag = True
    elif word.endswith("ing") and _contains_vowel(word[:-3]):
        word = word[:-3]
        flag = True
    if flag:
        if word.endswith(("at", "bl", "iz")):
            word += "e"
        elif _ends_double_consonant(word) and word[-1] not in "lsz":
            word = word[:-1]
        elif _measure(word) == 1 and _ends_cvc(word):
            word += "e"

    if word.endswith("y") and _contains_vowel(word[:-1]):
        word = word[:-1] + "i"

    word = _replace_suffix(word, _STEP2)
    word = _replace_suffix(word, _STEP3)
    for suffix in _STEP4:
        if word.endswith(suffix):
            base = word[: -len(suffix)]
            if suffix == "ion" and not base.endswith(("s", "t")):
                continue
            if _measure(base) > 1:
                word = base
            break

    if word.endswith("e"):
        base = word[:-1]
        if _measure(base) > 1 or (_measure(base) == 1 and not _ends_cvc(base)):
            word = base
    if _measure(word) > 1 and _ends_double_consonant(word) and word.endswith("l"):
        word = word[:-1]
    return word


def stem_term(term: str) -> str:
    """Fold one token, including the ``-ies``/``-ic`` family that Porter splits."""

    stemmed = porter_stem(term)
    if len(stemmed) > 3 and stemmed.endswith("i"):
        stemmed = stemmed[:-1]
    return stemmed


__all__ = ["porter_stem", "stem_term"]
