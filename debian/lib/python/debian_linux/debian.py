from __future__ import annotations

import collections
import collections.abc
import enum
import functools
import os.path
import re
import typing
import warnings

from typing import Iterable, Self


class Changelog(list):
    _top_rules = r"""
^
(?P<source>
    \w[-+0-9a-z.]+
)
\ 
\(
(?P<version>
    [^\(\)\ \t]+
)
\)
\s+
(?P<distribution>
    [-+0-9a-zA-Z.]+
)
\;\s+urgency=
(?P<urgency>
    \w+
)
(?:,|\n)
"""
    _top_re = re.compile(_top_rules, re.X)
    _bottom_rules = r"""
^
\ --\ 
(?P<maintainer>
    \S(?:\ ?\S)*
)
\ \ 
(?P<date>
    (.*)
)
\n
"""
    _bottom_re = re.compile(_bottom_rules, re.X)
    _ignore_re = re.compile(r'^(?:  |\s*\n)')

    class Entry(object):
        __slot__ = ('distribution', 'source', 'version', 'urgency',
                    'maintainer', 'date')

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    def __init__(self, dir='', version=None, file=None):
        if version is None:
            version = Version
        if file:
            self._parse(version, file)
        else:
            with open(os.path.join(dir, "debian/changelog"),
                      encoding="UTF-8") as f:
                self._parse(version, f)

    def _parse(self, version, f):
        top_match = None
        line_no = 0

        for line in f:
            line_no += 1

            if self._ignore_re.match(line):
                pass
            elif top_match is None:
                top_match = self._top_re.match(line)
                if not top_match:
                    raise Exception('invalid top line %d in changelog' %
                                    line_no)
                try:
                    v = version(top_match.group('version'))
                except Exception:
                    if not len(self):
                        raise
                    v = Version(top_match.group('version'))
            else:
                bottom_match = self._bottom_re.match(line)
                if not bottom_match:
                    raise Exception('invalid bottom line %d in changelog' %
                                    line_no)

                self.append(self.Entry(
                    distribution=top_match.group('distribution'),
                    source=top_match.group('source'),
                    version=v,
                    urgency=top_match.group('urgency'),
                    maintainer=bottom_match.group('maintainer'),
                    date=bottom_match.group('date')))
                top_match = bottom_match = None


class Version(object):
    _epoch_re = re.compile(r'\d+$')
    _upstream_re = re.compile(r'[0-9][A-Za-z0-9.+\-:~]*$')
    _revision_re = re.compile(r'[A-Za-z0-9+.~]+$')

    def __init__(self, version):
        try:
            split = version.index(':')
        except ValueError:
            epoch, rest = None, version
        else:
            epoch, rest = version[0:split], version[split+1:]
        try:
            split = rest.rindex('-')
        except ValueError:
            upstream, revision = rest, None
        else:
            upstream, revision = rest[0:split], rest[split+1:]
        if (epoch is not None and not self._epoch_re.match(epoch)) or \
           not self._upstream_re.match(upstream) or \
           (revision is not None and not self._revision_re.match(revision)):
            raise RuntimeError(u"Invalid debian version")
        self.epoch = epoch and int(epoch)
        self.upstream = upstream
        self.revision = revision

    def __str__(self):
        return self.complete

    @property
    def complete(self):
        if self.epoch is not None:
            return u"%d:%s" % (self.epoch, self.complete_noepoch)
        return self.complete_noepoch

    @property
    def complete_noepoch(self):
        if self.revision is not None:
            return u"%s-%s" % (self.upstream, self.revision)
        return self.upstream

    @property
    def debian(self):
        from warnings import warn
        warn(u"debian argument was replaced by revision", DeprecationWarning,
             stacklevel=2)
        return self.revision


class VersionLinux(Version):
    _upstream_re = re.compile(r"""
(?P<version>
    \d+\.\d+
)
(?P<update>
    (?:\.\d+)?
    (?:-[a-z]+\d+)?
)
(?:
    ~
    (?P<modifier>
        .+?
    )
)?
(?:
    \.dfsg\.
    (?P<dfsg>
        \d+
    )
)?
$
    """, re.X)
    _revision_re = re.compile(r"""
\d+
(\.\d+)?
(?:
    (?P<revision_experimental>
        ~exp\d+
    )
    |
    (?P<revision_security>
        (?:[~+]deb\d+u\d+)+
    )?
    (?P<revision_backports>
        ~bpo\d+\+\d+
    )?
    |
    (?P<revision_other>
        .+?
    )
)
(?:\+b\d+)?
$
    """, re.X)

    def __init__(self, version):
        super(VersionLinux, self).__init__(version)
        up_match = self._upstream_re.match(self.upstream)
        rev_match = self._revision_re.match(self.revision)
        if up_match is None or rev_match is None:
            raise RuntimeError(u"Invalid debian linux version")
        d = up_match.groupdict()
        self.linux_modifier = d['modifier']
        self.linux_version = d['version']
        if d['modifier'] is not None:
            assert not d['update']
            self.linux_upstream = '-'.join((d['version'], d['modifier']))
        else:
            self.linux_upstream = d['version']
        self.linux_upstream_full = self.linux_upstream + d['update']
        self.linux_dfsg = d['dfsg']
        d = rev_match.groupdict()
        self.linux_revision_experimental = d['revision_experimental'] and True
        self.linux_revision_security = d['revision_security'] and True
        self.linux_revision_backports = d['revision_backports'] and True
        self.linux_revision_other = d['revision_other'] and True


class PackageArchitecture(set[str]):
    def __init__(
        self,
        v: str | Iterable[str] | None = None,
        /,
    ) -> None:
        if v:
            if isinstance(v, str):
                v = re.split(r'\s+', v.strip())
            self |= frozenset(v)

    def __str__(self) -> str:
        return ' '.join(sorted(self))


class PackageDescription:
    short: list[str]
    long: list[str]

    def __init__(
        self,
        v: str | Self | None = None,
        /,
    ) -> None:
        self.short = []
        self.long = []

        if v:
            if isinstance(v, str):
                desc_split = v.split('\n', 1)
                self.append_short(desc_split[0])
                if len(desc_split) == 2:
                    self.append(desc_split[1])
            else:
                self.short.extend(v.short)
                self.long.extend(v.long)

    def __str__(self) -> str:
        from .utils import TextWrapper
        wrap = TextWrapper(width=74, fix_sentence_endings=True).wrap
        short = ', '.join(self.short)
        long_pars = []
        for i in self.long:
            long_pars.append(wrap(i))
        long = '\n .\n '.join('\n '.join(i) for i in long_pars)
        return short + '\n ' + long if long else short

    def append(self, long: str) -> None:
        long = long.strip()
        if long:
            self.long.extend(long.split('\n.\n'))

    def append_short(self, short: str) -> None:
        for i in [i.strip() for i in short.split(',')]:
            if i:
                self.short.append(i)

    def extend(self, desc: PackageDescription) -> None:
        self.short.extend(desc.short)
        self.long.extend(desc.long)


class PackageRelationEntryOperator(enum.StrEnum):
    OP_LT = '<<'
    OP_LE = '<='
    OP_EQ = '='
    OP_NE = '!='
    OP_GE = '>='
    OP_GT = '>>'

    def __neg__(self) -> str:
        return {
            self.OP_LT: self.OP_GE,
            self.OP_LE: self.OP_GT,
            self.OP_EQ: self.OP_NE,
            self.OP_NE: self.OP_EQ,
            self.OP_GE: self.OP_LT,
            self.OP_GT: self.OP_LE,
        }[self]


class PackageRelationEntry:
    name: str
    operator: typing.Optional[PackageRelationEntryOperator]
    version: typing.Optional[str]
    arches: PackageArchitecture
    restrictions: PackageBuildRestrictFormula

    __re = re.compile(
        r'^(?P<name>\S+)'
        r'(?: \((?P<operator><<|<=|=|!=|>=|>>)\s*(?P<version>[^)]+)\))?'
        r'(?: \[(?P<arches>[^]]+)\])?'
        r'(?P<restrictions>(?: <[^>]+>)*)$'
    )

    def __init__(
        self,
        v: str | Self,
        /, *,
        name: str | None = None,
        arches: set[str] | None = None,
        restrictions: PackageBuildRestrictFormula | str | None = None,
    ) -> None:
        if isinstance(v, str):
            match = self.__re.match(v)
            if not match:
                raise RuntimeError('Unable to parse dependency "%s"' % v)

            self.name = name or match['name']

            if operator := match['operator']:
                self.operator = PackageRelationEntryOperator(operator)
            else:
                self.operator = None

            self.version = match['version']
            self.arches = PackageArchitecture(arches or match['arches'])
            self.restrictions = PackageBuildRestrictFormula(
                restrictions or match['restrictions'],
            )

        else:
            self.name = name or v.name
            self.operator = v.operator
            self.version = v.version
            self.arches = PackageArchitecture(arches or v.arches)
            self.restrictions = PackageBuildRestrictFormula(
                restrictions or v.restrictions
            )

    def __str__(self):
        ret = [self.name]
        if self.operator and self.version:
            ret.append(f'({self.operator} {self.version})')
        if self.arches:
            ret.append(f'[{self.arches}]')
        if self.restrictions:
            ret.append(str(self.restrictions))
        return ' '.join(ret)


class PackageRelationGroup(list[PackageRelationEntry]):
    def __init__(
        self,
        v: Iterable[PackageRelationEntry | str] | str | Self | None = None,
        /, *,
        arches: set[str] | None = None,
    ) -> None:
        if v:
            if isinstance(v, str):
                v = (i.strip() for i in re.split(r'\|', v.strip()))
            self.extend(PackageRelationEntry(i, arches=arches) for i in v if i)

    def __str__(self) -> str:
        return ' | '.join(str(i) for i in self)

    def _merge_eq(self, v: PackageRelationGroup) -> typing.Optional[PackageRelationGroup]:
        if all(
            (
                i.name == j.name and i.operator == j.operator
                and i.version == j.version and i.restrictions == j.restrictions
            ) for i, j in zip(self, v)
        ):
            return self
        return None


class PackageRelation(list[PackageRelationGroup]):
    def __init__(
        self,
        v: Iterable[PackageRelationGroup | str] | str | Self | None = None,
        /, *,
        arches: set[str] | None = None,
    ) -> None:
        if v:
            if isinstance(v, str):
                v = (i.strip() for i in re.split(r',', v.strip()))
            self.extend(PackageRelationGroup(i, arches=arches) for i in v if i)

    def __str__(self) -> str:
        return ', '.join(str(i) for i in self)

    def _merge_eq(self, v: PackageRelationGroup) -> typing.Optional[PackageRelationGroup]:
        for i in self:
            if i._merge_eq(v):
                return i
        return None

    def merge(
        self,
        v: PackageRelationGroup | Iterable[PackageRelationEntry | str] | str,
        /,
    ) -> None:
        v = PackageRelationGroup(v)
        if g := self._merge_eq(v):
            for i, j in zip(g, v):
                i.arches |= j.arches
        else:
            super().append(v)


class PackageBuildRestrictFormula(set):
    _re = re.compile(r' *<([^>]+)>(?: +|$)')

    def __init__(self, value=None):
        if value:
            self.update(value)

    def __str__(self):
        return ' '.join(f'<{i}>' for i in sorted(self))

    def add(self, value):
        if isinstance(value, str):
            value = PackageBuildRestrictList(value)
        elif not isinstance(value, PackageBuildRestrictList):
            raise ValueError("got %s" % type(value))
        super(PackageBuildRestrictFormula, self).add(value)

    def update(self, value):
        if isinstance(value, str):
            pos = 0
            for match in self._re.finditer(value):
                if match.start() != pos:
                    break
                pos = match.end()
            if pos != len(value):
                raise ValueError(f'invalid restriction formula "{value}"')
            value = (match.group(1) for match in self._re.finditer(value))
        for i in value:
            self.add(i)

    # TODO: union etc.


class PackageBuildRestrictList(frozenset):
    # values are established in frozenset.__new__ not __init__, so we
    # implement __new__ as well
    def __new__(cls, value=()):
        if isinstance(value, str):
            if not re.fullmatch(r'[^()\[\]<>,]+', value):
                raise ValueError(f'invalid restriction list "{value}"')
            value = (PackageBuildRestrictTerm(i) for i in value.split())
        else:
            for i in value:
                if not isinstance(i, PackageBuildRestrictTerm):
                    raise ValueError
        return super(PackageBuildRestrictList, cls).__new__(cls, value)

    def __str__(self):
        return ' '.join(str(i) for i in sorted(self))

    # TODO: union etc.


@functools.total_ordering
class PackageBuildRestrictTerm(object):
    def __init__(self, value):
        if not isinstance(value, str):
            raise ValueError
        match = re.fullmatch(r'(!?)([^()\[\]<>,!\s]+)', value)
        if not match:
            raise ValueError(f'invalid restriction term "{value}"')
        self.negated = bool(match.group(1))
        self.profile = match.group(2)

    def __str__(self):
        return ('!' if self.negated else '') + self.profile

    def __eq__(self, other):
        return (self.negated == other.negated
                and self.profile == other.profile)

    def __lt__(self, other):
        return (self.profile < other.profile
                or (self.profile == other.profile
                    and not self.negated and other.negated))

    def __hash__(self):
        return hash(self.profile) ^ int(self.negated)


def restriction_requires_profile(form, profile):
    # An empty restriction formula does not require any profile.
    # Otherwise, a profile is required if each restriction list
    # includes it without negation.
    if len(form) == 0:
        return False
    term = PackageBuildRestrictTerm(profile)
    for lst in form:
        if term not in lst:
            return False
    return True


class _ControlFileDict(collections.abc.MutableMapping):
    def __init__(self):
        self.__data = {}
        self.meta = {}

    def __getitem__(self, key):
        return self.__data[key]

    def __setitem__(self, key, value):
        if key.lower().startswith('meta-'):
            self.meta[key.lower()[5:]] = value
            return

        try:
            cls = self._fields[key]
            if not isinstance(value, cls):
                value = cls(value)
        except KeyError:
            warnings.warn(
                f'setting unknown field { key } in { type(self).__name__ }',
                stacklevel=2)
        self.__data[key] = value

    def __delitem__(self, key):
        del self.__data[key]

    def __iter__(self):
        keys = set(self.__data.keys())
        for key in self._fields.keys():
            if key in self.__data:
                keys.remove(key)
                yield key
        for key in sorted(keys):
            yield key

    def __len__(self):
        return len(self.__data)

    def setdefault(self, key):
        try:
            return self[key]
        except KeyError:
            try:
                ret = self[key] = self._fields[key]()
            except KeyError:
                warnings.warn(
                    f'setting unknown field { key } in { type(self).__name__ }',
                    stacklevel=2)
                ret = self[key] = ''
            return ret

    def copy(self):
        ret = self.__class__()
        ret.__data = self.__data.copy()
        ret.meta = self.meta.copy()
        return ret

    @classmethod
    def read_rfc822(cls, f):
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


class SourcePackage(_ControlFileDict):
    _fields = collections.OrderedDict((
        ('Source', str),
        ('Architecture', PackageArchitecture),
        ('Section', str),
        ('Priority', str),
        ('Maintainer', str),
        ('Uploaders', str),
        ('Standards-Version', str),
        ('Build-Depends', PackageRelation),
        ('Build-Depends-Arch', PackageRelation),
        ('Build-Depends-Indep', PackageRelation),
        ('Rules-Requires-Root', str),
        ('Homepage', str),
        ('Vcs-Browser', str),
        ('Vcs-Git', str),
        ('XS-Autobuild', str),
    ))


class BinaryPackage(_ControlFileDict):
    _fields = collections.OrderedDict((
        ('Package', str),
        ('Package-Type', str),  # for udeb only
        ('Architecture', PackageArchitecture),
        ('Section', str),
        ('Priority', str),
        # Build-Depends* fields aren't allowed for binary packages in
        # the real control file, but we move them to the source
        # package
        ('Build-Depends', PackageRelation),
        ('Build-Depends-Arch', PackageRelation),
        ('Build-Depends-Indep', PackageRelation),
        ('Build-Profiles', PackageBuildRestrictFormula),
        ('Built-Using', PackageRelation),
        ('Provides', PackageRelation),
        ('Pre-Depends', PackageRelation),
        ('Depends', PackageRelation),
        ('Recommends', PackageRelation),
        ('Suggests', PackageRelation),
        ('Replaces', PackageRelation),
        ('Breaks', PackageRelation),
        ('Conflicts', PackageRelation),
        ('Multi-Arch', str),
        ('Kernel-Version', str),  # for udeb only
        ('Description', PackageDescription),
        ('Homepage', str),
    ))


class TestsControl(_ControlFileDict):
    _fields = collections.OrderedDict((
        ('Tests', str),
        ('Test-Command', str),
        ('Architecture', PackageArchitecture),
        ('Restrictions', str),
        ('Features', str),
        ('Depends', PackageRelation),
        ('Tests-Directory', str),
        ('Classes', str),
    ))
