Name:		zfo-editor
Version:	0.3.2
Release:	3
Summary:	ZFO editor allows working with zfo forms 
License:	GPLv2
Group:		Office
URL:		http://labs.nic.cz/page/768/zfo-editor/
Source0:	http://labs.nic.cz/files/labs/zfo_editor/%{name}-%{version}.tar.gz
BuildRequires:	python-setuptools
BuildArch:	noarch
Requires:	python-lxml
Requires:	python-webkitgtk

%description
ZFO editor allows working with ZFO forms on systems which are not officially
or at all supported by SW602 602XML Filler. ZFO editor works with forms
present in ZFO/FO format excluding ISDS Databox-ZFO format, please use dsgui
for that.

%prep
%setup -q -n %{name}-%{version}

%build

%install
%{__rm} -rf %{buildroot}
python setup.py install --root %{buildroot}/
mv %{buildroot}/%{py_sitedir}/release.py %{buildroot}/%{py_sitedir}/zfoeditor/
sed -i 's/Office/Office;/g' %{buildroot}/%{_datadir}/applications/%{name}.desktop
sed -i 's/+xml/+xml;/g' %{buildroot}/%{_datadir}/applications/%{name}.desktop

%clean
%{__rm} -rf %{buildroot}

%files
%defattr(644,root,root,755)
%doc README INSTALL RELEASE_NOTES.txt
%dir %{_datadir}/zfoeditor
%{_datadir}/zfoeditor/*
%{py_sitedir}/zfoeditor/*
%{py_sitedir}/zfo_editor*
%{_datadir}/applications/%{name}*
%{_datadir}/mime/packages/%{name}*
%defattr(755,root,root,755)
%{_bindir}/zfo_editor


%changelog
* Sat Oct 15 2011 Tomas Kindl <supp@mandriva.org> 0.3.2-2mdv2012.0
+ Revision: 704791
- add missing requires

* Sat Oct 01 2011 Tomas Kindl <supp@mandriva.org> 0.3.2-1
+ Revision: 702226
- import zfo-editor


