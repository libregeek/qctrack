Name:           qctrack
Version:        0.1.0
Release:        1%{?dist}
Summary:        Audio quality scoring and fake-bitrate detection

License:        GPL-3.0-only
URL:            https://github.com/libregeek/qctrack
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-hatchling

# Native libraries are loaded by name with ctypes, so RPM cannot see them.
Requires:       mpg123-libs
Requires:       libvorbis
Requires:       libogg
Requires:       libebur128
Recommends:     python3-pillow

%description
qctrack scores the quality of MP3 and Ogg Vorbis files and flags fake
bitrates, such as a low-bitrate encode re-encoded at 320 kbps. It writes a
screen table and CSV, JSON and HTML reports.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files qctrack
install -Dpm 0644 man/qctrack.1 %{buildroot}%{_mandir}/man1/qctrack.1

%check
%pyproject_check_import
test "$(PYTHONPATH=%{buildroot}%{python3_sitelib} %{buildroot}%{_bindir}/qctrack --version)" = "qctrack %{version}"

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/qctrack
%{_mandir}/man1/qctrack.1*

%changelog
* Thu Nov 19 2026 libregeek - 0.1.0-1
- First package
