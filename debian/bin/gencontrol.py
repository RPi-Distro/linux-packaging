#!/usr/bin/python3

import sys
import json
import locale
import os
import os.path
import pathlib
import subprocess
import re
import tempfile
from typing import Any

from debian_linux import config
from debian_linux.debian import \
    PackageRelationEntry, PackageRelationGroup, \
    VersionLinux, BinaryPackage, TestsControl
from debian_linux.gencontrol import Gencontrol as Base, PackagesBundle, \
    iter_featuresets, iter_flavours
from debian_linux.utils import Templates

locale.setlocale(locale.LC_CTYPE, "C.UTF-8")


class Gencontrol(Base):
    disable_installer: bool
    disable_signed: bool

    tests_control_headers: TestsControl | None

    config_schema = {
        'build': {
            'signed-code': config.SchemaItemBoolean(),
            'vdso': config.SchemaItemBoolean(),
        },
        'description': {
            'parts': config.SchemaItemList(),
        },
        'image': {
            'configs': config.SchemaItemList(),
            'check-size': config.SchemaItemInteger(),
            'check-size-with-dtb': config.SchemaItemBoolean(),
            'check-uncompressed-size': config.SchemaItemInteger(),
            'depends': config.SchemaItemList(','),
            'provides': config.SchemaItemList(','),
            'suggests': config.SchemaItemList(','),
            'recommends': config.SchemaItemList(','),
            'conflicts': config.SchemaItemList(','),
            'breaks': config.SchemaItemList(','),
        },
        'packages': {
            'docs': config.SchemaItemBoolean(),
            'installer': config.SchemaItemBoolean(),
            'libc-dev': config.SchemaItemBoolean(),
            'meta': config.SchemaItemBoolean(),
            'tools-unversioned': config.SchemaItemBoolean(),
            'tools-versioned': config.SchemaItemBoolean(),
            'source': config.SchemaItemBoolean(),
        }
    }

    env_flags = [
        ('DEBIAN_KERNEL_DISABLE_INSTALLER', 'disable_installer', 'installer modules'),
        ('DEBIAN_KERNEL_DISABLE_SIGNED', 'disable_signed', 'signed code'),
    ]

    def __init__(self, config_dirs=["debian/config", "debian/config.local"],
                 template_dirs=["debian/templates"]) -> None:
        super(Gencontrol, self).__init__(
            config.ConfigCoreHierarchy(self.config_schema, config_dirs),
            Templates(template_dirs),
            VersionLinux)
        self.process_changelog()
        self.config_dirs = config_dirs

        for env, attr, desc in self.env_flags:
            setattr(self, attr, False)
            if os.getenv(env):
                if self.changelog[0].distribution == 'UNRELEASED':
                    import warnings
                    warnings.warn(f'Disable {desc} on request ({env} set)')
                    setattr(self, attr, True)
                else:
                    raise RuntimeError(
                        f'Unable to disable {desc} in release build ({env} set)')

    def _setup_makeflags(self, names, makeflags, data) -> None:
        for src, dst, optional in names:
            if src in data or not optional:
                makeflags[dst] = data[src]

    def do_main_setup(self, vars, makeflags) -> None:
        super(Gencontrol, self).do_main_setup(vars, makeflags)
        makeflags.update({
            'VERSION': self.version.linux_version,
            'UPSTREAMVERSION': self.version.linux_upstream,
            'ABINAME': self.abiname,
            'SOURCEVERSION': self.version.complete,
        })
        makeflags['SOURCE_BASENAME'] = vars['source_basename']
        makeflags['SOURCE_SUFFIX'] = vars['source_suffix']

        # Prepare to generate debian/tests/control
        self.tests_control = self.templates.get_tests_control('main.tests-control', vars)
        self.tests_control_image = None
        self.tests_control_headers = None

    def do_main_makefile(self, makeflags) -> None:
        for featureset in iter_featuresets(self.config):
            makeflags_featureset = makeflags.copy()
            makeflags_featureset['FEATURESET'] = featureset

            self.bundle.makefile.add_rules(f'source_{featureset}',
                                           'source', makeflags_featureset)
            self.bundle.makefile.add_deps('source', [f'source_{featureset}'])

        makeflags = makeflags.copy()
        makeflags['ALL_FEATURESETS'] = ' '.join(iter_featuresets(self.config))
        super().do_main_makefile(makeflags)

    def do_main_packages(self, vars, makeflags) -> None:
        self.bundle.add('main', (), makeflags, vars)

        # Only build the metapackages if their names won't exactly match
        # the packages they depend on
        do_meta = self.config.merge('packages').get('meta', True) \
            and vars['source_suffix'] != '-' + vars['version']

        if self.config.merge('packages').get('docs', True):
            self.bundle.add('docs', (), makeflags, vars)
            if do_meta:
                self.bundle.add('docs.meta', (), makeflags, vars)
        if self.config.merge('packages').get('source', True):
            self.bundle.add('sourcebin', (), makeflags, vars)
            if do_meta:
                self.bundle.add('sourcebin.meta', (), makeflags, vars)

        if self.config.merge('packages').get('libc-dev', True):
            libcdev_kernelarches = set()
            libcdev_multiarches = set()
            for arch in iter(self.config['base', ]['arches']):
                libcdev_kernelarch = self.config['base', arch]['kernel-arch']
                libcdev_multiarch = subprocess.check_output(
                    ['dpkg-architecture', '-f', '-a', arch,
                     '-q', 'DEB_HOST_MULTIARCH'],
                    stderr=subprocess.DEVNULL,
                    encoding='utf-8').strip()
                libcdev_kernelarches.add(libcdev_kernelarch)
                libcdev_multiarches.add(f'{libcdev_multiarch}:{libcdev_kernelarch}')

            libcdev_makeflags = makeflags.copy()
            libcdev_makeflags['ALL_LIBCDEV_KERNELARCHES'] = ' '.join(sorted(libcdev_kernelarches))
            libcdev_makeflags['ALL_LIBCDEV_MULTIARCHES'] = ' '.join(sorted(libcdev_multiarches))

            self.bundle.add('libc-dev', (), libcdev_makeflags, vars)

    def do_indep_featureset_setup(self, vars, makeflags, featureset) -> None:
        makeflags['LOCALVERSION'] = vars['localversion']
        kernel_arches = set()
        for arch in iter(self.config['base', ]['arches']):
            if self.config.get_merge('base', arch, featureset, None,
                                     'flavours'):
                kernel_arches.add(self.config['base', arch]['kernel-arch'])
        makeflags['ALL_KERNEL_ARCHES'] = ' '.join(sorted(list(kernel_arches)))

        vars['featureset_desc'] = ''
        if featureset != 'none':
            desc = self.config[('description', None, featureset)]
            desc_parts = desc['parts']
            vars['featureset_desc'] = (' with the %s featureset' %
                                       desc['part-short-%s' % desc_parts[0]])

    def do_indep_featureset_packages(self, featureset, vars, makeflags) -> None:
        self.bundle.add('headers.featureset', (featureset, ), makeflags, vars)

    arch_makeflags = (
        ('kernel-arch', 'KERNEL_ARCH', False),
    )

    def do_arch_setup(self, vars, makeflags, arch) -> None:
        config_base = self.config.merge('base', arch)

        self._setup_makeflags(self.arch_makeflags, makeflags, config_base)

        try:
            gnu_type = subprocess.check_output(
                ['dpkg-architecture', '-f', '-a', arch,
                 '-q', 'DEB_HOST_GNU_TYPE'],
                stderr=subprocess.DEVNULL,
                encoding='utf-8')
        except subprocess.CalledProcessError:
            # This sometimes happens for the newest ports :-/
            print('W: Unable to get GNU type for %s' % arch, file=sys.stderr)
        else:
            vars['gnu-type-package'] = gnu_type.strip().replace('_', '-')

    def do_arch_packages(self, arch, vars, makeflags) -> None:
        if not self.disable_signed:
            build_signed = self.config.merge('build', arch) \
                                      .get('signed-code', False)
        else:
            build_signed = False

        if build_signed:
            # Make sure variables remain
            vars['signedtemplate_binaryversion'] = '@signedtemplate_binaryversion@'
            vars['signedtemplate_sourceversion'] = '@signedtemplate_sourceversion@'

            self.bundle.add('signed-template', (arch,), makeflags, vars, arch=arch)

            bundle_signed = self.bundles[f'signed-{arch}'] = \
                PackagesBundle(f'signed-{arch}', self.templates)
            bundle_signed.packages['source'] = \
                self.templates.get_source_control('signed.source.control', vars)[0]

            with bundle_signed.open('source/lintian-overrides', 'w') as f:
                f.write(self.substitute(
                    self.templates.get('signed.source.lintian-overrides'), vars))

            with bundle_signed.open('changelog.head', 'w') as f:
                dist = self.changelog[0].distribution
                urgency = self.changelog[0].urgency
                f.write(f'''\
linux-signed-{vars['arch']} (@signedtemplate_sourceversion@) {dist}; urgency={urgency}

  * Sign kernel from {self.changelog[0].source} @signedtemplate_binaryversion@
''')

        if self.config['base', arch].get('featuresets') and \
           self.config.merge('packages').get('source', True):
            self.bundle.add('config', (arch, ), makeflags, vars)

        if self.config.merge('packages').get('tools-unversioned', True):
            self.bundle.add('tools-unversioned', (arch, ), makeflags, vars)

        if self.config.merge('packages').get('tools-versioned', True):
            self.bundle.add('tools-versioned', (arch, ), makeflags, vars)

    def do_featureset_setup(self, vars, makeflags, arch, featureset) -> None:
        vars['localversion_headers'] = vars['localversion']
        makeflags['LOCALVERSION_HEADERS'] = vars['localversion_headers']

        self.default_flavour = self.config.merge('base', arch, featureset) \
                                          .get('default-flavour')
        if self.default_flavour is not None:
            if featureset != 'none':
                raise RuntimeError("default-flavour set for %s %s,"
                                   " but must only be set for featureset none"
                                   % (arch, featureset))
            if self.default_flavour \
               not in iter_flavours(self.config, arch, featureset):
                raise RuntimeError("default-flavour %s for %s %s does not exist"
                                   % (self.default_flavour, arch, featureset))

        self.quick_flavour = self.config.merge('base', arch, featureset) \
                                        .get('quick-flavour')

    flavour_makeflags_base = (
        ('compiler', 'COMPILER', False),
        ('compiler-filename', 'COMPILER', True),
        ('kernel-arch', 'KERNEL_ARCH', False),
        ('cflags', 'KCFLAGS', True),
        ('kernel-deb-arch', 'KERNEL_DEB_ARCH', True),
        ('kernel-gnu-type', 'KERNEL_GNU_TYPE', True),
        ('compat-deb-arch', 'COMPAT_DEB_ARCH', True),
        ('compat-gnu-type', 'COMPAT_GNU_TYPE', True),
    )

    flavour_makeflags_build = (
        ('image-file', 'IMAGE_FILE', True),
    )

    flavour_makeflags_image = (
        ('install-stem', 'IMAGE_INSTALL_STEM', True),
    )

    flavour_makeflags_other = (
        ('localversion', 'LOCALVERSION', False),
        ('localversion-image', 'LOCALVERSION_IMAGE', True),
    )

    def do_flavour_setup(self, vars, makeflags, arch, featureset, flavour) -> None:
        config_base = self.config.merge('base', arch, featureset, flavour)
        config_build = self.config.merge('build', arch, featureset, flavour)
        config_description = self.config.merge('description', arch, featureset,
                                               flavour)
        config_image = self.config.merge('image', arch, featureset, flavour)

        vars['flavour'] = vars['localversion'][1:]
        vars['class'] = config_description['hardware']
        vars['longclass'] = (config_description.get('hardware-long')
                             or vars['class'])

        vars['localversion-image'] = vars['localversion']
        override_localversion = config_image.get('override-localversion', None)
        if override_localversion is not None:
            vars['localversion-image'] = (vars['localversion_headers'] + '-'
                                          + override_localversion)
        vars['image-stem'] = config_image.get('install-stem')

        self._setup_makeflags(self.flavour_makeflags_base, makeflags,
                              config_base)
        self._setup_makeflags(self.flavour_makeflags_build, makeflags,
                              config_build)
        self._setup_makeflags(self.flavour_makeflags_image, makeflags,
                              config_image)
        self._setup_makeflags(self.flavour_makeflags_other, makeflags, vars)

    def do_flavour_packages(self, arch, featureset,
                            flavour, vars, makeflags) -> None:
        ruleid = (arch, featureset, flavour)

        packages_headers = (
            self.bundle.add('headers', ruleid, makeflags, vars, arch=arch)
        )
        assert len(packages_headers) == 1

        do_meta = self.config.merge('packages').get('meta', True)
        config_entry_base = self.config.merge('base', arch, featureset,
                                              flavour)
        config_entry_build = self.config.merge('build', arch, featureset,
                                               flavour)
        config_entry_description = self.config.merge('description', arch,
                                                     featureset, flavour)
        config_entry_packages = self.config.merge('packages', arch, featureset,
                                                  flavour)

        def config_entry_image(key, *args, **kwargs) -> Any:
            return self.config.get_merge(
                'image', arch, featureset, flavour, key, *args, **kwargs)

        compiler = config_entry_base.get('compiler', 'gcc')

        relation_compiler = PackageRelationEntry(compiler)

        relation_compiler_header = PackageRelationGroup([relation_compiler])

        # Generate compiler build-depends for native:
        # gcc-13 [arm64] <!cross !pkg.linux.nokernel>
        self.bundle.packages['source']['Build-Depends-Arch'].merge([
            PackageRelationEntry(
                relation_compiler,
                arches={arch},
                restrictions='<!cross !pkg.linux.nokernel>',
            )
        ])

        # Generate compiler build-depends for cross:
        # gcc-13-aarch64-linux-gnu [arm64] <cross !pkg.linux.nokernel>
        self.bundle.packages['source']['Build-Depends-Arch'].merge([
            PackageRelationEntry(
                relation_compiler,
                name=f'{relation_compiler.name}-{vars["gnu-type-package"]}',
                arches={arch},
                restrictions='<cross !pkg.linux.nokernel>',
            )
        ])

        # Generate compiler build-depends for kernel:
        # gcc-13-hppa64-linux-gnu [hppa] <!pkg.linux.nokernel>
        if gnutype := config_entry_base.get('kernel-gnu-type'):
            self.bundle.packages['source']['Build-Depends-Arch'].merge([
                PackageRelationEntry(
                    relation_compiler,
                    name=f'{relation_compiler.name}-{gnutype}',
                    arches={arch},
                    restrictions='<!pkg.linux.nokernel>',
                )
            ])

        # Generate compiler build-depends for compat:
        # gcc-arm-linux-gnueabihf [arm64] <!pkg.linux.nokernel>
        # XXX: Linux uses various definitions for this, all ending with "gcc", not $CC
        if gnutype := config_entry_base.get('compat-gnu-type'):
            self.bundle.packages['source']['Build-Depends-Arch'].merge([
                PackageRelationEntry(
                    f'gcc-{gnutype}',
                    arches={arch},
                    restrictions='<!pkg.linux.nokernel>',
                )
            ])

        packages_own = []

        if not self.disable_signed:
            build_signed = config_entry_build.get('signed-code')
        else:
            build_signed = False

        if build_signed:
            bundle_signed = self.bundles[f'signed-{arch}']
        else:
            bundle_signed = self.bundle

        vars.setdefault('desc', None)

        packages_image = []

        if build_signed:
            packages_image.extend(
                bundle_signed.add('signed.image', ruleid, makeflags, vars, arch=arch))
            packages_image.extend(
                self.bundle.add('image-unsigned', ruleid, makeflags, vars, arch=arch))

        else:
            packages_image.extend(bundle_signed.add('image', ruleid, makeflags, vars, arch=arch))

        for field in ('Depends', 'Provides', 'Suggests', 'Recommends',
                      'Conflicts', 'Breaks'):
            for i in config_entry_image(field.lower(), ()):
                for package_image in packages_image:
                    package_image.setdefault(field).merge(
                        PackageRelationGroup(i, arches={arch})
                    )

        for field in ('Depends', 'Suggests', 'Recommends'):
            for i in config_entry_image(field.lower(), ()):
                group = PackageRelationGroup(i, arches={arch})
                for entry in group:
                    if entry.operator is not None:
                        entry.operator = -entry.operator
                        for package_image in packages_image:
                            package_image.setdefault('Breaks').append(PackageRelationGroup([entry]))

        desc_parts = self.config.get_merge('description', arch, featureset,
                                           flavour, 'parts')
        if desc_parts:
            # XXX: Workaround, we need to support multiple entries of the same
            # name
            parts = list(set(desc_parts))
            parts.sort()
            for package_image in packages_image:
                desc = package_image['Description']
                for part in parts:
                    desc.append(config_entry_description['part-long-' + part])
                    desc.append_short(config_entry_description
                                      .get('part-short-' + part, ''))

        packages_headers[0]['Depends'].merge(relation_compiler_header)
        packages_own.extend(packages_image)
        packages_own.extend(packages_headers)

        # The image meta-packages will depend on signed linux-image
        # packages where applicable, so should be built from the
        # signed source packages The header meta-packages will also be
        # built along with the signed packages, to create a dependency
        # relationship that ensures src:linux and src:linux-signed-*
        # transition to testing together.
        if do_meta:
            packages_meta = (
                bundle_signed.add('image.meta', ruleid, makeflags, vars, arch=arch)
            )
            assert len(packages_meta) == 1
            packages_meta += (
                bundle_signed.add(build_signed and 'signed.headers.meta' or 'headers.meta',
                                  ruleid, makeflags, vars, arch=arch)
            )
            assert len(packages_meta) == 2

            if flavour == self.default_flavour \
               and not self.vars['source_suffix']:
                packages_meta[0].setdefault('Provides') \
                                .append('linux-image-generic')
                packages_meta[1].setdefault('Provides') \
                                .append('linux-headers-generic')

            packages_own.extend(packages_meta)

        if config_entry_build.get('vdso', False):
            makeflags['VDSO'] = True

        packages_own.extend(
            self.bundle.add('image-dbg', ruleid, makeflags, vars, arch=arch)
        )
        if do_meta:
            packages_own.extend(
                self.bundle.add('image-dbg.meta', ruleid, makeflags, vars, arch=arch)
            )

        # In a quick build, only build the quick flavour (if any).
        if flavour != self.quick_flavour:
            for package in packages_own:
                package['Build-Profiles'][0].neg.add('pkg.linux.quick')

        tests_control = self.templates.get_tests_control('image.tests-control', vars)[0]
        tests_control['Depends'].merge(
            PackageRelationGroup(package_image['Package'],
                                 arches={arch}))
        if self.tests_control_image:
            for i in tests_control['Depends']:
                self.tests_control_image['Depends'].merge(i)
        else:
            self.tests_control_image = tests_control
            self.tests_control.append(tests_control)

        if flavour == (self.quick_flavour or self.default_flavour):
            if not self.tests_control_headers:
                self.tests_control_headers = \
                        self.templates.get_tests_control('headers.tests-control', vars)[0]
                self.tests_control.append(self.tests_control_headers)
            assert self.tests_control_headers is not None
            self.tests_control_headers['Architecture'].add(arch)
            self.tests_control_headers['Depends'].merge(
                PackageRelationGroup(packages_headers[0]['Package'],
                                     arches={arch}))

        def get_config(*entry_name) -> Any:
            entry_real = ('image',) + entry_name
            entry = self.config.get(entry_real, None)
            if entry is None:
                return None
            return entry.get('configs', None)

        def check_config_default(fail, f) -> list[str]:
            for d in self.config_dirs[::-1]:
                f1 = d + '/' + f
                if os.path.exists(f1):
                    return [f1]
            if fail:
                raise RuntimeError("%s unavailable" % f)
            return []

        def check_config_files(files) -> list[str]:
            ret = []
            for f in files:
                for d in self.config_dirs[::-1]:
                    f1 = d + '/' + f
                    if os.path.exists(f1):
                        ret.append(f1)
                        break
                else:
                    raise RuntimeError("%s unavailable" % f)
            return ret

        def check_config(default, fail, *entry_name) -> list[str]:
            configs = get_config(*entry_name)
            if configs is None:
                return check_config_default(fail, default)
            return check_config_files(configs)

        kconfig = check_config('config', True)
        # XXX: We have no way to override kernelarch-X configs
        kconfig.extend(check_config_default(False,
                       "kernelarch-%s/config" % config_entry_base['kernel-arch']))
        kconfig.extend(check_config("%s/config" % arch, True, arch))
        kconfig.extend(check_config("%s/config.%s" % (arch, flavour), False,
                                    arch, None, flavour))
        kconfig.extend(check_config("featureset-%s/config" % featureset, False,
                                    None, featureset))
        kconfig.extend(check_config("%s/%s/config" % (arch, featureset), False,
                                    arch, featureset))
        kconfig.extend(check_config("%s/%s/config.%s" %
                                    (arch, featureset, flavour), False,
                                    arch, featureset, flavour))
        makeflags['KCONFIG'] = ' '.join(kconfig)
        makeflags['KCONFIG_OPTIONS'] = ''
        # Add "salt" to fix #872263
        makeflags['KCONFIG_OPTIONS'] += \
            ' -o "BUILD_SALT=\\"%(abiname)s%(localversion)s\\""' % vars

        merged_config = ('debian/build/config.%s_%s_%s' %
                         (arch, featureset, flavour))
        self.bundle.makefile.add_cmds(merged_config,
                                      ["$(MAKE) -f debian/rules.real %s %s" %
                                       (merged_config, makeflags)])

        if not self.disable_installer and config_entry_packages.get('installer'):
            with tempfile.TemporaryDirectory(prefix='linux-gencontrol') as config_dir:
                base_path = pathlib.Path('debian/installer').absolute()
                config_path = pathlib.Path(config_dir)
                (config_path / 'modules').symlink_to(base_path / 'modules')
                (config_path / 'package-list').symlink_to(base_path / 'package-list')

                with (config_path / 'kernel-versions').open('w') as versions:
                    versions.write(f'{arch} - {vars["flavour"]} - - -\n')

                # Add udebs using kernel-wedge
                kw_env = os.environ.copy()
                kw_env['KW_DEFCONFIG_DIR'] = config_dir
                kw_env['KW_CONFIG_DIR'] = config_dir
                kw_proc = subprocess.Popen(
                    ['kernel-wedge', 'gen-control', vars['abiname']],
                    stdout=subprocess.PIPE,
                    text=True,
                    env=kw_env)
                udeb_packages_base = BinaryPackage.read_rfc822(kw_proc.stdout)
                kw_proc.wait()
                if kw_proc.returncode != 0:
                    raise RuntimeError('kernel-wedge exited with code %d' %
                                       kw_proc.returncode)

            udeb_packages = []
            for package_base in udeb_packages_base:
                package = package_base.copy()
                # kernel-wedge currently chokes on Build-Profiles so add it now
                package['Build-Profiles'] = (
                    '<!noudeb !pkg.linux.nokernel !pkg.linux.quick>')
                package.meta['rules-target'] = 'installer'
                udeb_packages.append(package)

            makeflags_local = makeflags.copy()
            makeflags_local['IMAGE_PACKAGE_NAME'] = udeb_packages[0]['Package']

            bundle_signed.add_packages(
                udeb_packages,
                (arch, featureset, flavour),
                makeflags_local, arch=arch,
            )

            if build_signed:
                udeb_packages = []
                # XXX This is a hack to exclude the udebs from
                # the package list while still being able to
                # convince debhelper and kernel-wedge to go
                # part way to building them.
                for package_base in udeb_packages_base:
                    package = package_base.copy()
                    # kernel-wedge currently chokes on Build-Profiles so add it now
                    package['Build-Profiles'] = (
                        '<pkg.linux.udeb-unsigned-test-build !noudeb'
                        ' !pkg.linux.nokernel !pkg.linux.quick>')
                    package.meta['rules-target'] = 'installer-test'
                    udeb_packages.append(package)

                self.bundle.add_packages(
                    udeb_packages,
                    (arch, featureset, flavour),
                    makeflags, arch=arch, check_packages=False,
                )

    def process_changelog(self) -> None:
        version = self.version = self.changelog[0].version

        if self.changelog[0].distribution == 'UNRELEASED':
            self.abiname = f'{version.linux_upstream}+unreleased'
        elif self.changelog[0].distribution == 'experimental':
            self.abiname = f'{version.linux_upstream}'
        elif version.linux_revision_backports:
            self.abiname = f'{version.linux_upstream_full}+bpo'
        else:
            self.abiname = f'{version.linux_upstream_full}'

        self.vars = {
            'upstreamversion': self.version.linux_upstream,
            'version': self.version.linux_version,
            'version_complete': self.version.complete,
            'source_basename': re.sub(r'-[\d.]+$', '',
                                      self.changelog[0].source),
            'source_upstream': self.version.upstream,
            'source_package': self.changelog[0].source,
            'abiname': self.abiname,
        }
        self.vars['source_suffix'] = \
            self.changelog[0].source[len(self.vars['source_basename']):]
        self.config['version', ] = {'source': self.version.complete,
                                    'upstream': self.version.linux_upstream,
                                    'abiname_base': self.abiname,
                                    'abiname': self.abiname}

        distribution = self.changelog[0].distribution
        if distribution in ('unstable', ):
            if version.linux_revision_experimental or \
               version.linux_revision_backports or \
               version.linux_revision_other:
                raise RuntimeError("Can't upload to %s with a version of %s" %
                                   (distribution, version))
        if distribution in ('experimental', ):
            if not version.linux_revision_experimental:
                raise RuntimeError("Can't upload to %s with a version of %s" %
                                   (distribution, version))
        if distribution.endswith('-security') or distribution.endswith('-lts'):
            if version.linux_revision_backports or \
               version.linux_revision_other:
                raise RuntimeError("Can't upload to %s with a version of %s" %
                                   (distribution, version))
        if distribution.endswith('-backports'):
            if not version.linux_revision_backports:
                raise RuntimeError("Can't upload to %s with a version of %s" %
                                   (distribution, version))

    def write(self) -> None:
        self.write_config()
        super().write()
        self.write_tests_control()
        self.write_signed()

    def write_config(self) -> None:
        f = open("debian/config.defines.dump", 'wb')
        self.config.dump(f)
        f.close()

    def write_signed(self) -> None:
        for bundle in self.bundles.values():
            pkg_sign_entries = {}

            for p in bundle.packages.values():
                if pkg_sign_pkg := p.meta.get('sign-package'):
                    pkg_sign_entries[pkg_sign_pkg] = {
                        'trusted_certs': [],
                        'files': [
                            {
                                'sig_type': e.split(':', 1)[-1],
                                'file': e.split(':', 1)[0],
                            }
                            for e in p.meta['sign-files'].split()
                        ],
                    }

            if pkg_sign_entries:
                with bundle.path('files.json').open('w') as f:
                    json.dump({'packages': pkg_sign_entries}, f, indent=2)

    def write_tests_control(self) -> None:
        self.bundle.write_rfc822(open("debian/tests/control", 'w'),
                                 self.tests_control)


if __name__ == '__main__':
    Gencontrol()()
