import pytest

from .debian import (
    Version,
    VersionLinux,
    PackageArchitecture,
    PackageDescription,
    PackageRelationEntry,
    PackageRelationGroup,
    PackageRelation,
)


class TestVersion:
    def test_native(self):
        v = Version('1.2+c~4')
        assert v.epoch is None
        assert v.upstream == '1.2+c~4'
        assert v.revision is None
        assert v.complete == '1.2+c~4'
        assert v.complete_noepoch == '1.2+c~4'

    def test_nonnative(self):
        v = Version('1-2+d~3')
        assert v.epoch is None
        assert v.upstream == '1'
        assert v.revision == '2+d~3'
        assert v.complete == '1-2+d~3'
        assert v.complete_noepoch == '1-2+d~3'

    def test_native_epoch(self):
        v = Version('5:1.2.3')
        assert v.epoch == 5
        assert v.upstream == '1.2.3'
        assert v.revision is None
        assert v.complete == '5:1.2.3'
        assert v.complete_noepoch == '1.2.3'

    def test_nonnative_epoch(self):
        v = Version('5:1.2.3-4')
        assert v.epoch == 5
        assert v.upstream == '1.2.3'
        assert v.revision == '4'
        assert v.complete == '5:1.2.3-4'
        assert v.complete_noepoch == '1.2.3-4'

    def test_multi_hyphen(self):
        v = Version('1-2-3')
        assert v.epoch is None
        assert v.upstream == '1-2'
        assert v.revision == '3'
        assert v.complete == '1-2-3'

    def test_multi_colon(self):
        v = Version('1:2:3')
        assert v.epoch == 1
        assert v.upstream == '2:3'
        assert v.revision is None

    def test_invalid_epoch(self):
        with pytest.raises(RuntimeError):
            Version('a:1')
        with pytest.raises(RuntimeError):
            Version('-1:1')
        with pytest.raises(RuntimeError):
            Version('1a:1')

    def test_invalid_upstream(self):
        with pytest.raises(RuntimeError):
            Version('1_2')
        with pytest.raises(RuntimeError):
            Version('1/2')
        with pytest.raises(RuntimeError):
            Version('a1')
        with pytest.raises(RuntimeError):
            Version('1 2')

    def test_invalid_revision(self):
        with pytest.raises(RuntimeError):
            Version('1-2_3')
        with pytest.raises(RuntimeError):
            Version('1-2/3')
        with pytest.raises(RuntimeError):
            Version('1-2:3')


class TestVersionLinux:
    def test_stable(self):
        v = VersionLinux('1.2.3-4')
        assert v.linux_version == '1.2'
        assert v.linux_upstream == '1.2'
        assert v.linux_upstream_full == '1.2.3'
        assert v.linux_modifier is None
        assert v.linux_dfsg is None
        assert not v.linux_revision_experimental
        assert not v.linux_revision_security
        assert not v.linux_revision_backports
        assert not v.linux_revision_other

    def test_rc(self):
        v = VersionLinux('1.2~rc3-4')
        assert v.linux_version == '1.2'
        assert v.linux_upstream == '1.2-rc3'
        assert v.linux_upstream_full == '1.2-rc3'
        assert v.linux_modifier == 'rc3'
        assert v.linux_dfsg is None
        assert not v.linux_revision_experimental
        assert not v.linux_revision_security
        assert not v.linux_revision_backports
        assert not v.linux_revision_other

    def test_dfsg(self):
        v = VersionLinux('1.2~rc3.dfsg.1-4')
        assert v.linux_version == '1.2'
        assert v.linux_upstream == '1.2-rc3'
        assert v.linux_upstream_full == '1.2-rc3'
        assert v.linux_modifier == 'rc3'
        assert v.linux_dfsg == '1'
        assert not v.linux_revision_experimental
        assert not v.linux_revision_security
        assert not v.linux_revision_backports
        assert not v.linux_revision_other

    def test_experimental(self):
        v = VersionLinux('1.2~rc3-4~exp5')
        assert v.linux_upstream_full == '1.2-rc3'
        assert v.linux_revision_experimental
        assert not v.linux_revision_security
        assert not v.linux_revision_backports
        assert not v.linux_revision_other

    def test_security(self):
        v = VersionLinux('1.2.3-4+deb10u1')
        assert v.linux_upstream_full == '1.2.3'
        assert not v.linux_revision_experimental
        assert v.linux_revision_security
        assert not v.linux_revision_backports
        assert not v.linux_revision_other

    def test_backports(self):
        v = VersionLinux('1.2.3-4~bpo9+10')
        assert v.linux_upstream_full == '1.2.3'
        assert not v.linux_revision_experimental
        assert not v.linux_revision_security
        assert v.linux_revision_backports
        assert not v.linux_revision_other

    def test_security_backports(self):
        v = VersionLinux('1.2.3-4+deb10u1~bpo9+10')
        assert v.linux_upstream_full == '1.2.3'
        assert not v.linux_revision_experimental
        assert v.linux_revision_security
        assert v.linux_revision_backports
        assert not v.linux_revision_other

    def test_lts_backports(self):
        # Backport during LTS, as an extra package in the -security
        # suite.  Since this is not part of a -backports suite it
        # shouldn't get the linux_revision_backports flag.
        v = VersionLinux('1.2.3-4~deb9u10')
        assert v.linux_upstream_full == '1.2.3'
        assert not v.linux_revision_experimental
        assert v.linux_revision_security
        assert not v.linux_revision_backports
        assert not v.linux_revision_other

    def test_lts_backports_2(self):
        # Same but with two security extensions in the revision.
        v = VersionLinux('1.2.3-4+deb10u1~deb9u10')
        assert v.linux_upstream_full == '1.2.3'
        assert not v.linux_revision_experimental
        assert v.linux_revision_security
        assert not v.linux_revision_backports
        assert not v.linux_revision_other

    def test_binnmu(self):
        v = VersionLinux('1.2.3-4+b1')
        assert not v.linux_revision_experimental
        assert not v.linux_revision_security
        assert not v.linux_revision_backports
        assert not v.linux_revision_other

    def test_other_revision(self):
        v = VersionLinux('4.16.5-1+revert+crng+ready')  # from #898087
        assert not v.linux_revision_experimental
        assert not v.linux_revision_security
        assert not v.linux_revision_backports
        assert v.linux_revision_other

    def test_other_revision_binnmu(self):
        v = VersionLinux('4.16.5-1+revert+crng+ready+b1')
        assert not v.linux_revision_experimental
        assert not v.linux_revision_security
        assert not v.linux_revision_backports
        assert v.linux_revision_other


class TestPackageArchitecture:
    def test_init(self):
        a = PackageArchitecture()
        assert a == set()

    def test_init_str(self):
        a = PackageArchitecture(' foo  bar\tbaz ')
        assert a == {'foo', 'bar', 'baz'}

    def test_init_iter(self):
        a = PackageArchitecture(('foo', 'bar'))
        assert a == {'foo', 'bar'}

    def test_init_self(self):
        a = PackageArchitecture(PackageArchitecture(('foo', 'bar')))
        assert a == {'foo', 'bar'}

    def test_str(self):
        a = PackageArchitecture(('foo', 'bar'))
        assert str(a) == 'bar foo'


class TestPackageDescription:
    def test_init(self):
        a = PackageDescription()
        assert a.short == []
        assert a.long == []

    def test_init_str(self):
        a = PackageDescription('Short\nLong1\n.\nLong2')
        assert a.short == ['Short']
        assert a.long == ['Long1', 'Long2']

    def test_init_self(self):
        a = PackageDescription(PackageDescription('Short\nLong1\n.\nLong2'))
        assert a.short == ['Short']
        assert a.long == ['Long1', 'Long2']

    def test_str(self):
        a = PackageDescription('Short\nLong1\n.\nLong2')
        assert str(a) == 'Short\n Long1\n .\n Long2'


class TestPackageRelationEntry:
    def test_init_str(self):
        a = PackageRelationEntry('package (>=version) [arch2 arch1] <profile1 >')
        assert a.name == 'package'
        assert a.version == 'version'
        assert a.arches == {'arch1', 'arch2'}
        # TODO: assert a.profiles
        assert str(a) == 'package (>= version) [arch1 arch2] <profile1>'

    def test_init_self(self):
        a = PackageRelationEntry(PackageRelationEntry('package [arch2 arch1]'))
        assert a.name == 'package'
        assert a.arches == {'arch1', 'arch2'}
        assert str(a) == 'package [arch1 arch2]'


class TestPackageRelationGroup:
    def test_init(self):
        a = PackageRelationGroup()
        assert a == []

    def test_init_str(self):
        a = PackageRelationGroup('foo | bar')
        assert len(a) == 2
        assert a[0].name == 'foo'
        assert a[1].name == 'bar'

    def test_init_iter_entry(self):
        a = PackageRelationGroup((PackageRelationEntry('foo'), PackageRelationEntry('bar')))
        assert len(a) == 2
        assert a[0].name == 'foo'
        assert a[1].name == 'bar'

    def test_init_iter_str(self):
        a = PackageRelationGroup(('foo', 'bar'))
        assert len(a) == 2
        assert a[0].name == 'foo'
        assert a[1].name == 'bar'

    def test_init_self(self):
        a = PackageRelationGroup(PackageRelationGroup(['foo', 'bar']))
        assert len(a) == 2
        assert a[0].name == 'foo'
        assert a[1].name == 'bar'

    def test_str(self):
        a = PackageRelationGroup('foo|  bar')
        assert str(a) == 'foo | bar'


class TestPackageRelation:
    def test_init(self):
        a = PackageRelation()
        assert a == []

    def test_init_str(self):
        a = PackageRelation('foo1 | foo2, bar')
        assert len(a) == 2
        assert len(a[0]) == 2
        assert a[0][0].name == 'foo1'
        assert a[0][1].name == 'foo2'
        assert len(a[1]) == 1
        assert a[1][0].name == 'bar'

    def test_init_iter_entry(self):
        a = PackageRelation([[PackageRelationEntry('foo')], [PackageRelationEntry('bar')]])
        assert len(a) == 2
        assert len(a[0]) == 1
        assert a[0][0].name == 'foo'
        assert len(a[1]) == 1
        assert a[1][0].name == 'bar'

    def test_init_iter_str(self):
        a = PackageRelation(('foo', 'bar'))
        assert len(a) == 2
        assert len(a[0]) == 1
        assert a[0][0].name == 'foo'
        assert len(a[1]) == 1
        assert a[1][0].name == 'bar'

    def test_init_self(self):
        a = PackageRelation(PackageRelation(('foo', 'bar')))
        assert len(a) == 2
        assert len(a[0]) == 1
        assert a[0][0].name == 'foo'
        assert len(a[1]) == 1
        assert a[1][0].name == 'bar'

    def test_str(self):
        a = PackageRelation('foo ,bar')
        assert str(a) == 'foo, bar'
