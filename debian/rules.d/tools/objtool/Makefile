include $(top_rulesdir)/Makefile.inc

all:
# For now, only supported target architecture is x86.  Later we'll need to build
# for multiple targets and add a wrapper, same as for modpost.
# objtool always uses HOSTCC, HOSTLD, and HOSTAR; we need to override
# these on the command line to make cross-builds work.  But it also
# builds fixdep which still needs to be native in a cross-build.  Set
# REALHOSTCC and REALHOSTLD variables which will be used for fixdep.
	$(MAKE) -C $(top_srcdir)/tools/objtool O=$(CURDIR) HOSTARCH=$(KERNEL_ARCH) ARCH=x86 HOSTCC=$(CC) HOSTLD=$(CROSS_COMPILE)ld HOSTAR=$(CROSS_COMPILE)ar REALHOSTCC=gcc REALHOSTLD=ld V=1 EXTRA_CFLAGS='$(CFLAGS) $(CPPFLAGS)'

install:
	install -D -m755 objtool $(DESTDIR)/$(installdir)/objtool
