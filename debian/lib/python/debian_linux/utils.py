import io
import os
import re
import textwrap
import typing

from .debian import SourcePackage, BinaryPackage, TestsControl


class Templates(object):
    dirs: list[str]
    _cache: dict[str, str]

    def __init__(self, dirs: list[str] = ["debian/templates"]) -> None:
        self.dirs = dirs

        self._cache = {}

    def _read(self, name: str) -> typing.Any:
        pkgid, name = name.rsplit('.', 1)

        for suffix in ['.in', '']:
            for dir in self.dirs:
                filename = "%s/%s.%s%s" % (dir, pkgid, name, suffix)
                if os.path.exists(filename):
                    with open(filename, 'r', encoding='utf-8') as f:
                        mode = os.stat(f.fileno()).st_mode
                        return (f.read(), mode)

        raise KeyError(name)

    def _get(self, key: str) -> typing.Any:
        try:
            return self._cache[key]
        except KeyError:
            self._cache[key] = value = self._read(key)
            return value

    def get(self, key: str) -> str:
        return self._get(key)[0]

    def get_mode(self, key: str) -> str:
        return self._get(key)[1]

    def get_control(self, key: str) -> BinaryPackage:
        return BinaryPackage.read_rfc822(io.StringIO(self.get(key)))

    def get_source_control(self, key: str) -> SourcePackage:
        return SourcePackage.read_rfc822(io.StringIO(self.get(key)))

    def get_tests_control(self, key: str) -> TestsControl:
        return TestsControl.read_rfc822(io.StringIO(self.get(key)))


class TextWrapper(textwrap.TextWrapper):
    wordsep_re = re.compile(
        r'(\s+|'                                  # any whitespace
        r'(?<=[\w\!\"\'\&\.\,\?])-{2,}(?=\w))')   # em-dash
