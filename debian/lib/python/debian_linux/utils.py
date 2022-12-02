import os
import re
import textwrap
import typing


class Templates(object):
    dirs: list[str]
    _cache: dict[str, typing.Any]

    def __init__(self, dirs: list[str] = ["debian/templates"]) -> None:
        self.dirs = dirs

        self._cache = {}

    def __getitem__(self, key: str):
        ret = self.get(key)
        if ret is not None:
            return ret
        raise KeyError(key)

    def _read(self, name: str) -> typing.Any:
        pkgid, name = name.rsplit('.', 1)

        for suffix in ['.in', '']:
            for dir in self.dirs:
                filename = "%s/%s.%s%s" % (dir, pkgid, name, suffix)
                if os.path.exists(filename):
                    with open(filename, 'r', encoding='utf-8') as f:
                        mode = os.stat(f.fileno()).st_mode
                        if name == 'control':
                            if pkgid == 'source':
                                return (read_control_source(f), mode)
                            else:
                                return (read_control(f), mode)
                        if name == 'tests-control':
                            return (read_tests_control(f), mode)
                        return (f.read(), mode)

    def _get(self, key: str) -> typing.Any:
        try:
            return self._cache[key]
        except KeyError:
            self._cache[key] = value = self._read(key)
            return value

    def get(self, key: str, default: typing.Any = None) -> typing.Any:
        value = self._get(key)
        if value is None:
            return default
        return value[0]

    def get_mode(self, key: str) -> typing.Any:
        value = self._get(key)
        if value is None:
            return None
        return value[1]


def read_control_source(f):
    from .debian import SourcePackage
    return _read_rfc822(f, SourcePackage)


def read_control(f):
    from .debian import BinaryPackage
    return _read_rfc822(f, BinaryPackage)


def read_tests_control(f):
    from .debian import TestsControl
    return _read_rfc822(f, TestsControl)


def _read_rfc822(f, cls):
    entries = []
    eof = False

    while not eof:
        e = cls()
        last = None
        lines = []
        while True:
            line = f.readline()
            if not line:
                eof = True
                break
            # Strip comments rather than trying to preserve them
            if line[0] == '#':
                continue
            line = line.strip('\n')
            if not line:
                break
            if line[0] in ' \t':
                if not last:
                    raise ValueError(
                        'Continuation line seen before first header')
                lines.append(line.lstrip())
                continue
            if last:
                e[last] = '\n'.join(lines)
            i = line.find(':')
            if i < 0:
                raise ValueError(u"Not a header, not a continuation: ``%s''" %
                                 line)
            last = line[:i]
            lines = [line[i + 1:].lstrip()]
        if last:
            e[last] = '\n'.join(lines)
        if e:
            entries.append(e)

    return entries


class TextWrapper(textwrap.TextWrapper):
    wordsep_re = re.compile(
        r'(\s+|'                                  # any whitespace
        r'(?<=[\w\!\"\'\&\.\,\?])-{2,}(?=\w))')   # em-dash
